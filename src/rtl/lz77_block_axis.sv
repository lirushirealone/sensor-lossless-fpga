`timescale 1ns / 1ps
// LZ5Annnn command, n int16 low-half words, last on final sample.
// Return 32-bit tokens followed by a 32-byte LZT1 trailer, no padding.
module lz77_block_axis(
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
    localparam COMMAND=0,INPUT_DATA=1,WAIT_FRONT=2,MATCH=3,WAIT_MATCH=4,META_CRC=5,TRAILER=6;
    reg [2:0] state;
    reg protocol_error,active;
    reg [13:0] expected_count,input_count;
    reg [31:0] sequence_number,cycle_count,collect_count,stall_count,output_count;
    reg [31:0] saved_raw_crc,saved_adler,token_crc,metadata_crc;
    reg [223:0] trailer_head;
    reg [255:0] trailer;
    reg [4:0] crc_index;
    reg [2:0] trailer_word;
    wire front_ready,front_valid,front_last,front_done,front_error;
    wire [7:0] front_data;wire [13:0] front_count;wire [31:0] raw_crc,adler;
    wire matcher_ready,token_valid,token_last,matcher_done,matcher_error;
    wire [31:0] token_data,work_cycles,probes;wire [14:0] residual_count,token_count;
    wire expected_last=input_count+1'b1==expected_count;
    wire input_legal=s_axis_tdata[31:16]==0 && s_axis_tlast==expected_last;
    assign s_axis_tready=aresetn&&!protocol_error&&!front_error&&!matcher_error&&
        (state==COMMAND || (state==INPUT_DATA && front_ready));
    wire input_fire=s_axis_tvalid&&s_axis_tready;
    assign m_axis_tvalid=aresetn&&!protocol_error&&!matcher_error &&
        ((state==MATCH && token_valid)||state==TRAILER);
    assign m_axis_tdata=state==MATCH ? token_data : trailer>>(32*trailer_word);
    assign m_axis_tkeep=4'b1111;
    assign m_axis_tlast=state==TRAILER && trailer_word==7;
    wire output_fire=m_axis_tvalid&&m_axis_tready;
    delta_checksum_frontend front(
        .clk(aclk),.resetn(aresetn),.s_data(s_axis_tdata[15:0]),
        .s_valid(s_axis_tvalid&&state==INPUT_DATA&&!protocol_error&&input_legal),
        .s_last(expected_last),.s_ready(front_ready),
        .m_data(front_data),.m_valid(front_valid),.m_last(front_last),
        .m_ready(matcher_ready&&(state==INPUT_DATA||state==WAIT_FRONT)&&!protocol_error),
        .done(front_done),.protocol_error(front_error),.sample_count(front_count),
        .raw_crc32(raw_crc),.residual_adler32(adler));
    lz77_matcher matcher(
        .clk(aclk),.resetn(aresetn),.s_data(front_data),
        .s_valid(front_valid&&(state==INPUT_DATA||state==WAIT_FRONT)&&!protocol_error),
        .s_last(front_last),.s_ready(matcher_ready),
        .m_data(token_data),.m_valid(token_valid),.m_last(token_last),
        .m_ready(m_axis_tready&&state==MATCH&&!protocol_error),
        .done(matcher_done),.protocol_error(matcher_error),.byte_count(residual_count),.token_count(token_count),
        .compute_cycles(work_cycles),.search_probes(probes));
    function automatic [31:0] crc_byte(input [31:0] old,input [7:0] value);
        reg [31:0] x;integer i;
        begin x=old^{24'd0,value};for(i=0;i<8;i=i+1)x=x[0] ? (x>>1)^32'hedb88320 : x>>1;crc_byte=x;end
    endfunction
    wire [31:0] next_token_crc=crc_byte(crc_byte(crc_byte(crc_byte(token_crc,token_data[7:0]),token_data[15:8]),token_data[23:16]),token_data[31:24]);
    wire [31:0] next_metadata_crc=crc_byte(metadata_crc,trailer_head>>(8*crc_index));
    always @(posedge aclk)begin
        if(!aresetn)begin
            state<=COMMAND;protocol_error<=0;active<=0;expected_count<=0;input_count<=0;
            sequence_number<=0;cycle_count<=0;collect_count<=0;stall_count<=0;output_count<=0;
            saved_raw_crc<=0;saved_adler<=0;token_crc<=32'hffffffff;metadata_crc<=32'hffffffff;
            trailer_head<=0;trailer<=0;crc_index<=0;trailer_word<=0;
            stats_total_cycles<=0;stats_collect_cycles<=0;stats_process_cycles<=0;
            stats_output_stalls<=0;stats_input_samples<=0;stats_output_bytes<=0;
            stats_sequence<=0;stats_input_gaps<=0;stats_matcher_work_cycles<=0;stats_search_probes<=0;
        end else begin
            if(front_error||matcher_error)protocol_error<=1;
            if(active)cycle_count<=cycle_count+1'b1;
            if(m_axis_tvalid&&!m_axis_tready)stall_count<=stall_count+1'b1;
            if(input_fire)begin
                if(state==COMMAND)begin
                    if(s_axis_tlast||s_axis_tdata[31:16]!=16'h4c5a||s_axis_tdata[15:0]==0||s_axis_tdata[15:0]>8192)
                        protocol_error<=1;
                    else begin
                        state<=INPUT_DATA;active<=1;expected_count<=s_axis_tdata[13:0];input_count<=0;
                        cycle_count<=1;collect_count<=0;stall_count<=0;output_count<=0;token_crc<=32'hffffffff;
                    end
                end else if(!input_legal)protocol_error<=1;
                else begin
                    input_count<=input_count+1'b1;
                    if(expected_last)begin state<=WAIT_FRONT;collect_count<=cycle_count+1'b1;end
                end
            end
            if(front_done&&state==WAIT_FRONT&&!protocol_error)begin
                if(front_count!=expected_count||residual_count!=({1'b0,expected_count}<<1))protocol_error<=1;
                else begin saved_raw_crc<=raw_crc;saved_adler<=adler;state<=MATCH;end
            end
            if(output_fire)begin
                output_count<=output_count+4;
                if(state==MATCH)begin token_crc<=next_token_crc;if(token_last)state<=WAIT_MATCH;end
                else if(state==TRAILER)begin
                    if(trailer_word==7)begin
                        state<=COMMAND;active<=0;sequence_number<=sequence_number+1'b1;
                        stats_total_cycles<=cycle_count+1'b1;stats_collect_cycles<=collect_count;
                        stats_process_cycles<=cycle_count+1'b1-collect_count;stats_output_stalls<=stall_count;
                        stats_input_samples<={18'd0,input_count};stats_output_bytes<=output_count+4;
                        stats_sequence<=sequence_number+1'b1;stats_input_gaps<=collect_count-input_count-1'b1;
                        stats_matcher_work_cycles<=work_cycles;stats_search_probes<=probes;
                    end else trailer_word<=trailer_word+1'b1;
                end
            end
            if(matcher_done&&state==WAIT_MATCH&&!protocol_error)begin
                if(!token_count||token_count>residual_count)protocol_error<=1;
                else begin
                    trailer_head<={~token_crc,saved_adler,saved_raw_crc,17'd0,token_count,sequence_number+32'd1,2'd0,expected_count,8'd0,8'd1,32'h31545a4c};
                    metadata_crc<=32'hffffffff;crc_index<=0;state<=META_CRC;
                end
            end
            if(state==META_CRC)begin
                metadata_crc<=next_metadata_crc;
                if(crc_index==27)begin trailer<={~next_metadata_crc,trailer_head};trailer_word<=0;state<=TRAILER;end
                else crc_index<=crc_index+1'b1;
            end
        end
    end
endmodule
