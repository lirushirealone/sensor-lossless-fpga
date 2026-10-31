`timescale 1ns / 1ps
// Bounded batch of one/two independent ORIGINAL LZT1 blocks, followed by
// per-block counters and a CRC-protected directory. Only the final footer
// word has external TLAST. No cross-block dictionary or altered matching.
module lz77_axis(
    (* X_INTERFACE_PARAMETER="ASSOCIATED_BUSIF S_AXIS:M_AXIS, ASSOCIATED_RESET aresetn" *)
    (* X_INTERFACE_INFO="xilinx.com:signal:clock:1.0 aclk CLK" *) input wire aclk,
    (* X_INTERFACE_PARAMETER="POLARITY ACTIVE_LOW" *)
    (* X_INTERFACE_INFO="xilinx.com:signal:reset:1.0 aresetn RST" *) input wire aresetn,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TDATA" *) input wire [31:0] s_axis_tdata,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TVALID" *) input wire s_axis_tvalid,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TREADY" *) output wire s_axis_tready,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TLAST" *) input wire s_axis_tlast,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 M_AXIS TDATA" *) output wire [31:0] m_axis_tdata,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 M_AXIS TKEEP" *) output wire [3:0] m_axis_tkeep,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 M_AXIS TVALID" *) output wire m_axis_tvalid,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 M_AXIS TREADY" *) input wire m_axis_tready,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 M_AXIS TLAST" *) output wire m_axis_tlast,
    output reg [31:0] stats_total_cycles,stats_collect_cycles,stats_process_cycles,
    output reg [31:0] stats_output_stalls,stats_input_samples,stats_output_bytes,
    output reg [31:0] stats_sequence,stats_input_gaps,
    output reg [31:0] stats_matcher_work_cycles,stats_search_probes
);
    localparam HEADER=0,CMD=1,DATA=2,WAIT_BLOCK=3,CAPTURE=4,COUNTERS=5,CRC=6,FOOTER=7;
    reg [2:0] state;
    reg error,active;
    reg [1:0] members;
    reg member;
    reg [13:0] count,received;
    reg [31:0] cycle_count,stall_count,out_bytes,in_words,block_bytes;
    reg [31:0] sizes[0:1];
    reg [31:0] counters[0:9];
    reg [31:0] sum_collect,sum_samples,sum_gaps,sum_work,sum_probes;
    reg [31:0] batch_sequence,payload_crc,head_crc;
    reg [223:0] footer_head;
    reg [255:0] footer;
    reg [4:0] crc_index;
    reg [3:0] word_index;
    wire inner_ready,inner_valid,inner_last;
    wire [31:0] inner_data;
    wire [3:0] inner_keep;
    wire [31:0] st[0:9];
    wire final_sample=received+1'b1==count;
    wire cmd_legal=s_axis_tdata[31:16]==16'h4c5a && s_axis_tdata[15:0]>0 && s_axis_tdata[15:0]<=8192 && !s_axis_tlast;
    wire data_legal=s_axis_tdata[31:16]==0 && s_axis_tlast==(final_sample && member+1==members);
    wire input_legal=(state==CMD && cmd_legal)||(state==DATA && data_legal);
    assign s_axis_tready=aresetn&&!error&&(state==HEADER||((state==CMD||state==DATA)&&inner_ready));
    wire input_fire=s_axis_tvalid&&s_axis_tready;
    assign m_axis_tvalid=aresetn&&!error&&((state==WAIT_BLOCK&&inner_valid)||state==COUNTERS||state==FOOTER);
    assign m_axis_tdata=state==WAIT_BLOCK ? inner_data : state==COUNTERS ? counters[word_index] : footer>>(32*word_index);
    assign m_axis_tkeep=4'hf;
    assign m_axis_tlast=state==FOOTER&&word_index==7;
    wire output_fire=m_axis_tvalid&&m_axis_tready;
    lz77_block_axis block_core(
        .aclk(aclk),.aresetn(aresetn),.s_axis_tdata(s_axis_tdata),
        .s_axis_tvalid(s_axis_tvalid&&input_legal&&!error),
        .s_axis_tready(inner_ready),.s_axis_tlast(state==DATA&&final_sample),
        .m_axis_tdata(inner_data),.m_axis_tkeep(inner_keep),.m_axis_tvalid(inner_valid),
        .m_axis_tlast(inner_last),.m_axis_tready(m_axis_tready&&state==WAIT_BLOCK&&!error),
        .stats_total_cycles(st[0]),.stats_collect_cycles(st[1]),.stats_process_cycles(st[2]),
        .stats_output_stalls(st[3]),.stats_input_samples(st[4]),.stats_output_bytes(st[5]),
        .stats_sequence(st[6]),.stats_input_gaps(st[7]),.stats_matcher_work_cycles(st[8]),.stats_search_probes(st[9]));
    function automatic [31:0] crc_byte(input [31:0] old,input [7:0] value);
        reg [31:0] x;integer i;
        begin x=old^{24'd0,value};for(i=0;i<8;i=i+1)x=x[0]?(x>>1)^32'hedb88320:x>>1;crc_byte=x;end
    endfunction
    wire [31:0] next_payload_crc=crc_byte(crc_byte(crc_byte(crc_byte(payload_crc,m_axis_tdata[7:0]),m_axis_tdata[15:8]),m_axis_tdata[23:16]),m_axis_tdata[31:24]);
    wire [31:0] next_head_crc=crc_byte(head_crc,footer_head>>(8*crc_index));
    integer j;
    always @(posedge aclk)begin
        if(!aresetn)begin
            state<=HEADER;error<=0;active<=0;members<=0;member<=0;count<=0;received<=0;
            cycle_count<=0;stall_count<=0;out_bytes<=0;in_words<=0;block_bytes<=0;
            sum_collect<=0;sum_samples<=0;sum_gaps<=0;sum_work<=0;sum_probes<=0;batch_sequence<=0;
            payload_crc<=32'hffffffff;head_crc<=32'hffffffff;footer_head<=0;footer<=0;crc_index<=0;word_index<=0;
            for(j=0;j<2;j=j+1)sizes[j]<=0;
            for(j=0;j<10;j=j+1)counters[j]<=0;
            stats_total_cycles<=0;stats_collect_cycles<=0;stats_process_cycles<=0;stats_output_stalls<=0;
            stats_input_samples<=0;stats_output_bytes<=0;stats_sequence<=0;stats_input_gaps<=0;
            stats_matcher_work_cycles<=0;stats_search_probes<=0;
        end else begin
            if(active)cycle_count<=cycle_count+1;
            if(m_axis_tvalid&&!m_axis_tready)stall_count<=stall_count+1;
            if(input_fire)begin
                in_words<=in_words+1;
                case(state)
                    HEADER:if(s_axis_tlast||s_axis_tdata[31:16]!=16'h4c42||s_axis_tdata[15:0]<1||s_axis_tdata[15:0]>2)error<=1;
                    else begin
                        members<=s_axis_tdata[1:0];member<=0;state<=CMD;active<=1;cycle_count<=1;
                        stall_count<=0;out_bytes<=0;in_words<=1;block_bytes<=0;sizes[0]<=0;sizes[1]<=0;
                        sum_collect<=1;sum_samples<=0;sum_gaps<=0;sum_work<=0;sum_probes<=0;payload_crc<=32'hffffffff;
                    end
                    CMD:if(!cmd_legal)error<=1;else begin count<=s_axis_tdata[13:0];received<=0;state<=DATA;end
                    DATA:if(!data_legal)error<=1;else begin received<=received+1;if(final_sample)state<=WAIT_BLOCK;end
                endcase
            end
            if(output_fire)begin
                out_bytes<=out_bytes+4;
                if(state!=FOOTER)payload_crc<=next_payload_crc;
                if(state==WAIT_BLOCK)begin
                    block_bytes<=block_bytes+4;
                    if(inner_last)begin sizes[member]<=block_bytes+4;state<=CAPTURE;end
                end else if(state==COUNTERS)begin
                    if(word_index==9)begin
                        if(member+1<members)begin member<=member+1;block_bytes<=0;state<=CMD;end
                        else begin
                            footer_head<={~next_payload_crc,in_words,sizes[1],sizes[0],batch_sequence+32'd1,16'd0,6'd0,members,8'd1,32'h31425a4c};
                            head_crc<=32'hffffffff;crc_index<=0;state<=CRC;
                        end
                    end else word_index<=word_index+1;
                end else if(state==FOOTER)begin
                    if(word_index==7)begin
                        state<=HEADER;active<=0;batch_sequence<=batch_sequence+1;
                        stats_total_cycles<=cycle_count+1;stats_collect_cycles<=sum_collect;
                        stats_process_cycles<=cycle_count+1-sum_collect;stats_output_stalls<=stall_count;
                        stats_input_samples<=sum_samples;stats_output_bytes<=out_bytes+4;stats_sequence<=batch_sequence+1;
                        stats_input_gaps<=sum_gaps;stats_matcher_work_cycles<=sum_work;stats_search_probes<=sum_probes;
                    end else word_index<=word_index+1;
                end
            end
            if(state==CAPTURE)begin
                for(j=0;j<10;j=j+1)counters[j]<=st[j];
                sum_collect<=sum_collect+st[1];sum_samples<=sum_samples+st[4];sum_gaps<=sum_gaps+st[7];
                sum_work<=sum_work+st[8];sum_probes<=sum_probes+st[9];word_index<=0;state<=COUNTERS;
            end
            if(state==CRC)begin
                head_crc<=next_head_crc;
                if(crc_index==27)begin footer<={~next_head_crc,footer_head};word_index<=0;state<=FOOTER;end
                else crc_index<=crc_index+1;
            end
        end
    end
endmodule
