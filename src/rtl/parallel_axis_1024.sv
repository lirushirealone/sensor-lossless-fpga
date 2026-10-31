`timescale 1ns / 1ps
// Identical MDC2 coding in 1/2/4 lanes; full-frame buffers decouple egress.
module parallel_axis_1024 # (parameter integer LANES=4) (
    (* X_INTERFACE_PARAMETER="ASSOCIATED_BUSIF S_AXIS:M_AXIS, ASSOCIATED_RESET aresetn" *)
    (* X_INTERFACE_INFO="xilinx.com:signal:clock:1.0 aclk CLK" *) input wire aclk,
    (* X_INTERFACE_PARAMETER="POLARITY ACTIVE_LOW" *)
    (* X_INTERFACE_INFO="xilinx.com:signal:reset:1.0 aresetn RST" *) input wire aresetn,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TDATA" *) input wire [31:0] s_axis_tdata,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TVALID" *) input wire s_axis_tvalid,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TREADY" *) output wire s_axis_tready,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TLAST" *) input wire s_axis_tlast,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 M_AXIS TDATA" *) output reg [31:0] m_axis_tdata,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 M_AXIS TKEEP" *) output reg [3:0] m_axis_tkeep,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 M_AXIS TVALID" *) output reg m_axis_tvalid,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 M_AXIS TREADY" *) input wire m_axis_tready,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 M_AXIS TLAST" *) output reg m_axis_tlast,
    output reg [31:0] stats_total_cycles,stats_collect_cycles,stats_process_cycles,
    output reg [31:0] stats_output_stalls,stats_input_samples,stats_output_bytes,
    output reg [31:0] stats_sequence,stats_input_gaps,
    output reg [31:0] stats_busy_lane_cycles,stats_max_active_lanes
);
    reg [10:0] fill_count[0:3],block_length[0:3];
    reg [31:0] channel_sequence[0:3];
    reg [3:0] queue_ready;
    reg packet_active,input_done,protocol_error;
    reg [3:0] expected_blocks,input_blocks,emitted_blocks;
    wire [1:0] input_channel=s_axis_tdata[17:16];
    wire input_block_last=s_axis_tdata[18];
    wire input_fire=s_axis_tvalid && s_axis_tready;
    wire sample_fire=input_fire && packet_active;
    assign s_axis_tready=aresetn && !protocol_error &&
        (!packet_active || (!input_done && !queue_ready[input_channel] && fill_count[input_channel]<1024));

    reg [1:0] scheduler_state[0:LANES-1],read_channel[0:LANES-1],rr_pointer[0:LANES-1];
    reg [10:0] read_index[0:LANES-1],read_length[0:LANES-1];
    wire [LANES-1:0] core_input_valid,core_input_ready,core_input_fire,read_last,ram_read;
    reg [LANES-1:0] selected_valid;
    reg [1:0] selected_channel[0:LANES-1];
    wire [9:0] ram_index[0:LANES-1];
    wire [15:0] queue_data[0:3];
    wire [7:0] core_data[0:LANES-1];
    wire [LANES-1:0] core_valid,core_ready,core_last,core_busy;
    reg [LANES-1:0] frame_ready;
    reg [11:0] frame_length[0:LANES-1],write_count[0:LANES-1];
    reg [31:0] partial_word[0:LANES-1];
    reg [31:0] frame_read_word[0:LANES-1];

    reg [2:0] drain_state;
    reg [1:0] drain_lane,drain_rr;
    reg [9:0] word_index;
    reg [11:0] drain_length;
    reg [31:0] egress_partial;
    reg [2:0] egress_count;
    wire word_slot=!m_axis_tvalid || m_axis_tready;
    wire output_fire=m_axis_tvalid && m_axis_tready;
    wire final_word=({word_index,2'b00}+12'd4>=drain_length);
    wire packet_last=emitted_blocks==expected_blocks-1'b1;
    wire [2:0] chunk_bytes=final_word ? drain_length-{word_index,2'b00} : 3'd4;
    wire [3:0] joined_count={1'b0,egress_count}+{1'b0,chunk_bytes};
    wire [63:0] joined_word={32'd0,egress_partial} | ({32'd0,frame_read_word[drain_lane]} << (8*egress_count));
    integer i,l,scan,idx,busy_count;
    reg chosen_valid;
    reg [1:0] chosen_lane;
    always @* begin
        chosen_valid=0; chosen_lane=0; idx=0; busy_count=0;
        for(scan=0;scan<LANES;scan=scan+1) begin
            idx=(drain_rr+scan)%LANES;
            if(!chosen_valid && frame_ready[idx]) begin chosen_valid=1; chosen_lane=idx; end
            busy_count=busy_count+core_busy[scan];
        end
    end

    genvar ch,c;
    generate for(ch=0;ch<4;ch=ch+1) begin: queues
        localparam integer OWNER=ch%LANES;
        (* ram_style="block" *) reg [15:0] samples[0:1023];
        reg [15:0] sample_read;
        assign queue_data[ch]=sample_read;
        always @(posedge aclk) begin
            if(aresetn && sample_fire && input_channel==ch)
                samples[fill_count[ch][9:0]]<=s_axis_tdata[15:0];
            if(aresetn && ram_read[OWNER] && read_channel[OWNER]==ch)
                sample_read<=samples[ram_index[OWNER]];
        end
    end endgenerate

    generate for(c=0;c<LANES;c=c+1) begin: lanes
        integer n,channel_idx;
        always @* begin
            selected_valid[c]=0; selected_channel[c]=0; channel_idx=0;
            for(n=0;n<4;n=n+1) begin
                channel_idx=(rr_pointer[c]+n)&3;
                if(!selected_valid[c] && channel_idx%LANES==c && queue_ready[channel_idx]) begin
                    selected_valid[c]=1; selected_channel[c]=channel_idx;
                end
            end
        end
        assign core_input_valid[c]=scheduler_state[c]==2;
        assign read_last[c]=read_index[c]==read_length[c]-1'b1;
        assign core_input_fire[c]=core_input_valid[c] && core_input_ready[c];
        assign ram_read[c]=scheduler_state[c]==1 || (core_input_fire[c] && !read_last[c]);
        assign ram_index[c]=scheduler_state[c]==1 ? 10'd0 : read_index[c][9:0]+10'd1;
        assign core_ready[c]=aresetn && !frame_ready[c] && write_count[c]<2072;
        sensor_compressor_1024 core(.clk(aclk),.resetn(aresetn),
            .s_tdata(queue_data[read_channel[c]]),.s_channel(read_channel[c]),
            .s_sequence(channel_sequence[read_channel[c]]+32'd1),
            .s_tvalid(core_input_valid[c]),.s_tready(core_input_ready[c]),
            .s_block_last(read_last[c]),.s_tlast(1'b0),
            .m_tdata(core_data[c]),.m_tvalid(core_valid[c]),.m_tready(core_ready[c]),
            .m_tlast(core_last[c]),.m_packet_last(),.busy(core_busy[c]));
        // Byte assembly writes one full 32-bit BRAM word, with zero tail padding.
        (* ram_style="block" *) reg [31:0] words[0:519];
        wire [31:0] appended=partial_word[c] | ({24'd0,core_data[c]} << (8*write_count[c][1:0]));
        always @(posedge aclk) begin
            if(aresetn && core_valid[c] && core_ready[c] && (write_count[c][1:0]==3 || core_last[c]))
                words[write_count[c][11:2]]<=appended;
            if(aresetn && drain_state==1 && drain_lane==c) frame_read_word[c]<=words[word_index];
        end
    end endgenerate

    always @(posedge aclk) begin
        if(!aresetn) begin
            packet_active<=0; input_done<=0; protocol_error<=0; queue_ready<=0;
            expected_blocks<=0; input_blocks<=0; emitted_blocks<=0;
            frame_ready<=0; drain_state<=0; drain_lane<=0; drain_rr<=0; word_index<=0; drain_length<=0;
            m_axis_tdata<=0; m_axis_tvalid<=0; m_axis_tkeep<=0; m_axis_tlast<=0;
            egress_partial<=0; egress_count<=0;
            for(i=0;i<4;i=i+1) begin fill_count[i]<=0; block_length[i]<=0; channel_sequence[i]<=0; end
            for(l=0;l<LANES;l=l+1) begin
                scheduler_state[l]<=0; read_channel[l]<=0; rr_pointer[l]<=l;
                read_index[l]<=0; read_length[l]<=0; frame_length[l]<=0; write_count[l]<=0; partial_word[l]<=0;
            end
        end else begin
            if(input_fire) begin
                if(!packet_active) begin
                    if(s_axis_tdata[31:8]!=24'h4d4300 || s_axis_tdata[7:0]==0 || s_axis_tdata[7:0]>8 || s_axis_tlast) protocol_error<=1;
                    else begin packet_active<=1; input_done<=0; expected_blocks<=s_axis_tdata[3:0]; input_blocks<=0; emitted_blocks<=0; end
                end else begin
                    fill_count[input_channel]<=fill_count[input_channel]+1'b1;
                    if(s_axis_tdata[31:19]!=0 || (fill_count[input_channel]==1023 && !input_block_last)) protocol_error<=1;
                    if(input_block_last) begin
                        queue_ready[input_channel]<=1; block_length[input_channel]<=fill_count[input_channel]+1'b1;
                        input_blocks<=input_blocks+1'b1;
                        if(input_blocks>=expected_blocks) protocol_error<=1;
                    end
                    if(s_axis_tlast) begin
                        input_done<=1;
                        if(!input_block_last || input_blocks+1'b1!=expected_blocks) protocol_error<=1;
                        for(i=0;i<4;i=i+1) if(i!=input_channel && !queue_ready[i] && fill_count[i]!=0) protocol_error<=1;
                    end
                end
            end
            for(l=0;l<LANES;l=l+1) begin
                if(!protocol_error) case(scheduler_state[l])
                    0: if(packet_active && selected_valid[l]) begin
                        read_channel[l]<=selected_channel[l];
                        read_length[l]<=block_length[selected_channel[l]]; read_index[l]<=0; scheduler_state[l]<=1;
                    end
                    1:scheduler_state[l]<=2;
                    2:if(core_input_fire[l]) begin
                        if(read_index[l]==0) channel_sequence[read_channel[l]]<=channel_sequence[read_channel[l]]+1'b1;
                        if(read_last[l]) begin
                            queue_ready[read_channel[l]]<=0; fill_count[read_channel[l]]<=0;
                            rr_pointer[l]<=read_channel[l]+1'b1; scheduler_state[l]<=0;
                        end else read_index[l]<=read_index[l]+1'b1;
                    end
                endcase
                if(core_valid[l] && core_ready[l]) begin
                    if(core_last[l]) begin
                        frame_ready[l]<=1; frame_length[l]<=write_count[l]+1'b1; write_count[l]<=0; partial_word[l]<=0;
                    end else begin
                        write_count[l]<=write_count[l]+1'b1;
                        partial_word[l]<=write_count[l][1:0]==3 ? 0 : partial_word[l] | ({24'd0,core_data[l]} << (8*write_count[l][1:0]));
                    end
                end
            end
            if(output_fire) m_axis_tvalid<=0;
            case(drain_state)
                0:if(chosen_valid) begin drain_lane<=chosen_lane; drain_length<=frame_length[chosen_lane]; word_index<=0; drain_state<=1; end
                1:drain_state<=2;
                2:if(word_slot) begin
                    if(joined_count>=4 || (final_word && packet_last)) begin
                        m_axis_tdata<=joined_word[31:0]; m_axis_tvalid<=1;
                        m_axis_tkeep<=joined_count<4 ? (4'b1111>>(4-joined_count)) : 4'b1111;
                        m_axis_tlast<=final_word && packet_last && joined_count<=4;
                        egress_partial<=joined_count>4 ? joined_word[63:32] : 0;
                        egress_count<=joined_count>4 ? joined_count-4 : 0;
                    end else begin egress_partial<=joined_word[31:0]; egress_count<=joined_count; end
                    if(final_word) begin
                        frame_ready[drain_lane]<=0; drain_rr<=(drain_lane+1)%LANES; emitted_blocks<=emitted_blocks+1'b1;
                        drain_state<=packet_last ? (joined_count>4 ? 3 : 4) : 0;
                    end else begin word_index<=word_index+1'b1; drain_state<=1; end
                end
                3:if(word_slot) begin
                    m_axis_tdata<=egress_partial; m_axis_tkeep<=4'b1111>>(4-egress_count);
                    m_axis_tvalid<=1; m_axis_tlast<=1; egress_count<=0; egress_partial<=0; drain_state<=4;
                end
                4:if(output_fire && m_axis_tlast) begin packet_active<=0; input_done<=0; drain_state<=0; end
            endcase
        end
    end

    reg perf_active,collected;
    reg [31:0] total_live,collect_live,samples_live,bytes_live,gaps_live,stalls_live,busy_live,max_busy_live;
    wire [2:0] word_bytes={2'd0,m_axis_tkeep[0]}+{2'd0,m_axis_tkeep[1]}+{2'd0,m_axis_tkeep[2]}+{2'd0,m_axis_tkeep[3]};
    always @(posedge aclk) begin
        if(!aresetn) begin
            perf_active<=0; collected<=0; total_live<=0; collect_live<=0; samples_live<=0; bytes_live<=0;
            gaps_live<=0; stalls_live<=0; busy_live<=0; max_busy_live<=0;
            stats_total_cycles<=0; stats_collect_cycles<=0; stats_process_cycles<=0; stats_output_stalls<=0;
            stats_input_samples<=0; stats_output_bytes<=0; stats_sequence<=0; stats_input_gaps<=0;
            stats_busy_lane_cycles<=0; stats_max_active_lanes<=0;
        end else if(!perf_active) begin
            if(sample_fire) begin
                perf_active<=1; collected<=s_axis_tlast; total_live<=1; collect_live<=1; samples_live<=1;
                bytes_live<=0; gaps_live<=0; stalls_live<=0; busy_live<=busy_count; max_busy_live<=busy_count;
            end
        end else begin
            total_live<=total_live+1'b1; busy_live<=busy_live+busy_count;
            if(busy_count>max_busy_live) max_busy_live<=busy_count;
            if(!collected) begin
                collect_live<=collect_live+1'b1;
                if(sample_fire) begin samples_live<=samples_live+1'b1; if(s_axis_tlast) collected<=1; end
                else gaps_live<=gaps_live+1'b1;
            end
            if(m_axis_tvalid && !m_axis_tready) stalls_live<=stalls_live+1'b1;
            if(output_fire) bytes_live<=bytes_live+word_bytes;
            if(output_fire && m_axis_tlast) begin
                perf_active<=0; stats_total_cycles<=total_live+1'b1; stats_collect_cycles<=collect_live;
                stats_process_cycles<=total_live+1'b1-collect_live; stats_input_samples<=samples_live;
                stats_output_bytes<=bytes_live+word_bytes; stats_input_gaps<=gaps_live;
                stats_output_stalls<=stalls_live; stats_sequence<=stats_sequence+expected_blocks;
                stats_busy_lane_cycles<=busy_live+busy_count;
                stats_max_active_lanes<=busy_count>max_busy_live ? busy_count : max_busy_live;
            end
        end
    end
endmodule
