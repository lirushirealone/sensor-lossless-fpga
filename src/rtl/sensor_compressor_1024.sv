`timescale 1ns / 1ps
// MDC2: two independent sample banks, exact mode costs, bounded byte output.
module sensor_compressor_1024 (
    input wire clk, resetn,
    input wire [15:0] s_tdata,
    input wire [1:0] s_channel,
    input wire [31:0] s_sequence,
    input wire s_tvalid, s_tlast, s_block_last,
    output wire s_tready,
    output reg [7:0] m_tdata,
    output reg m_tvalid, m_tlast,
    input wire m_tready,
    output wire busy,
    output wire m_packet_last
);
    localparam WAIT_BANK=0, START=1, HEADER=2, RAW=3, FIRST=4,
        FETCH=5, DIFFERENCE=6, RICE_START=7, UNARY=8, REMAINDER=9,
        GROUP_HEADER=10, GROUP_SAMPLE=11, FIELD=12, PAD=13, WAIT_LAST=14;
    reg [3:0] state;
    wire encoder_active=(state!=WAIT_BANK);
    (* ram_style="block" *) reg [15:0] samples[0:2047];
    reg [15:0] sample_read;
    reg fill_bank,read_bank,packet_pending,collect_packet_last;
    reg [1:0] bank_full,bank_ready,bank_packet_last;
    reg [10:0] bank_count[0:1];
    reg [31:0] bank_crc[0:1],bank_sequence[0:1];
    reg [1:0] bank_channel[0:1];
    reg [2:0] bank_mode[0:1];
    reg [4:0] bank_k[0:1];
    reg [11:0] bank_length[0:1];
    reg [2:0] collect_state;
    reg [10:0] collect_count;
    assign busy=encoder_active || collect_count!=0 || collect_state!=0;
    reg [15:0] previous_sample,previous_previous;
    reg [31:0] collect_crc,collect_sequence;
    reg [1:0] collect_channel;
    reg [26:0] costs0[0:16],costs1[0:16],best_cost0,best_cost1;
    reg [4:0] select_index,best_k0,best_k1;
    reg [14:0] group_cost0,group_cost1;
    reg [4:0] collect_width0,collect_width1,previous_width0,previous_width1;
    reg previous_width_valid;
    reg [2:0] collect_group_count;
    reg [1:0] candidate_index;
    reg [2:0] selected_mode;
    reg [4:0] selected_k;
    reg [11:0] selected_length;
    reg [10:0] count,sample_index;
    reg [11:0] payload_length,raw_position;
    reg [1:0] channel;
    reg [31:0] sequence_number,sample_crc,metadata_crc_state;
    reg [2:0] mode;
    reg [4:0] rice_k,header_position;
    reg encoder_packet_last,first_high;
    reg [15:0] encoder_previous,encoder_previous_previous;
    reg [16:0] mapped_value,quotient,remainder_value;
    reg [4:0] remainder_left;
    reg [7:0] bit_accumulator;
    reg [2:0] bits_used;
    reg signed [16:0] group_errors[0:3];
    reg [1:0] group_load_index,group_emit_index;
    reg [2:0] group_length;
    reg [4:0] group_width,encoder_previous_width;
    reg encoder_previous_width_valid,group_final,field_header;
    reg [16:0] field_value;
    reg [4:0] field_left;
    integer i;

    assign s_tready=resetn && collect_state==0 && !packet_pending &&
        (collect_count!=0 || !bank_full[fill_bank]);
    assign m_packet_last=m_tlast && encoder_packet_last;
    wire output_slot=!m_tvalid || m_tready;

    function automatic signed [16:0] residual;
        input [15:0] value,previous_value,older_value;
        input trend,has_history;
        reg signed [17:0] prediction;
        reg signed [16:0] clipped;
        begin
            prediction=$signed({{2{previous_value[15]}},previous_value});
            if(trend && has_history)
                prediction=(prediction<<<1)-$signed({{2{older_value[15]}},older_value});
            if(prediction>18'sd32767) clipped=17'sd32767;
            else if(prediction< -18'sd32768) clipped= -17'sd32768;
            else clipped=prediction[16:0];
            residual=$signed({value[15],value})-clipped;
        end
    endfunction
    function automatic [16:0] zigzag(input signed [16:0] error);
        begin zigzag=error<0 ? ((-error)<<1)-1'b1 : error<<1; end
    endfunction
    function automatic [4:0] signed_width(input signed [16:0] error);
        reg [16:0] magnitude;
        integer b;
        begin
            magnitude=error[16] ? ~error : error;
            signed_width=(error==0) ? 0 : 1;
            for(b=0;b<16;b=b+1) if(magnitude[b]) signed_width=b+2;
        end
    endfunction
    function automatic [31:0] crc_byte(input [31:0] crc,input [7:0] value);
        reg [31:0] c; integer b;
        begin
            c=crc^{24'd0,value};
            for(b=0;b<8;b=b+1) c=c[0] ? (c>>1)^32'hedb88320 : c>>1;
            crc_byte=c;
        end
    endfunction
    wire signed [16:0] incoming0=residual(s_tdata,previous_sample,previous_previous,0,collect_count>=2);
    wire signed [16:0] incoming1=residual(s_tdata,previous_sample,previous_previous,1,collect_count>=2);
    wire [16:0] mapped0=zigzag(incoming0),mapped1=zigzag(incoming1);
    wire [4:0] width0=signed_width(incoming0),width1=signed_width(incoming1);
    wire [4:0] max_width0=width0>collect_width0 ? width0 : collect_width0;
    wire [4:0] max_width1=width1>collect_width1 ? width1 : collect_width1;
    wire [2:0] new_group_count=collect_group_count+1'b1;
    wire end_input_block=s_block_last || s_tlast || collect_count==1023;
    wire [14:0] group_header_cost0=!previous_width_valid ? 15'd5 :
        ((max_width0+6'd1>=previous_width0 && previous_width0+6'd1>=max_width0) ? 15'd2 : 15'd7);
    wire [14:0] group_header_cost1=!previous_width_valid ? 15'd5 :
        ((max_width1+6'd1>=previous_width1 && previous_width1+6'd1>=max_width1) ? 15'd2 : 15'd7);
    reg [26:0] candidate_length;
    reg [2:0] candidate_mode;
    reg [4:0] candidate_k;
    always @* begin
        candidate_length=0; candidate_mode=0; candidate_k=0;
        case(candidate_index)
            0:begin candidate_length=2+((best_cost0+27'd7)>>3); candidate_mode=1; candidate_k=best_k0; end
            1:begin candidate_length=2+(({12'd0,group_cost0}+27'd7)>>3); candidate_mode=3; end
            2:begin candidate_length=2+((best_cost1+27'd7)>>3); candidate_mode=2; candidate_k=best_k1; end
            3:begin candidate_length=2+(({12'd0,group_cost1}+27'd7)>>3); candidate_mode=4; end
        endcase
    end

    always @(posedge clk) begin
        if(!resetn) begin
            collect_state<=0; collect_count<=0; collect_crc<=32'hffffffff;
            previous_sample<=0; previous_previous<=0; collect_sequence<=0; collect_channel<=0;
            fill_bank<=0; packet_pending<=0; collect_packet_last<=0;
            bank_full<=0; bank_ready<=0; bank_packet_last<=0;
            best_cost0<=27'h7ffffff; best_cost1<=27'h7ffffff;
            best_k0<=0; best_k1<=0; select_index<=0; candidate_index<=0;
            selected_mode<=0; selected_k<=0; selected_length<=0;
            group_cost0<=0; group_cost1<=0; collect_group_count<=0;
            collect_width0<=0; collect_width1<=0; previous_width0<=0; previous_width1<=0;
            previous_width_valid<=0;
            for(i=0;i<17;i=i+1) begin costs0[i]<=0; costs1[i]<=0; end
            for(i=0;i<2;i=i+1) begin
                bank_count[i]<=0; bank_crc[i]<=0; bank_sequence[i]<=0;
                bank_channel[i]<=0; bank_mode[i]<=0; bank_k[i]<=0; bank_length[i]<=0;
            end
        end else begin
            if(m_tvalid && m_tready && m_tlast) begin
                bank_full[read_bank]<=0;
                if(encoder_packet_last) packet_pending<=0;
            end
            case(collect_state)
                0:if(s_tvalid && s_tready) begin
                    bank_full[fill_bank]<=1;
                    if(collect_count==0) begin collect_channel<=s_channel; collect_sequence<=s_sequence; end
                    previous_previous<=previous_sample; previous_sample<=s_tdata;
                    collect_crc<=crc_byte(crc_byte(collect_crc,s_tdata[7:0]),s_tdata[15:8]);
                    if(collect_count!=0) begin
                        for(i=0;i<17;i=i+1) begin
                            costs0[i]<=costs0[i]+(mapped0>>i)+i+1;
                            costs1[i]<=costs1[i]+(mapped1>>i)+i+1;
                        end
                        if(collect_group_count==3 || end_input_block) begin
                            group_cost0<=group_cost0+group_header_cost0+({8'd0,max_width0}*{10'd0,new_group_count});
                            group_cost1<=group_cost1+group_header_cost1+({8'd0,max_width1}*{10'd0,new_group_count});
                            previous_width0<=max_width0; previous_width1<=max_width1;
                            previous_width_valid<=1; collect_width0<=0; collect_width1<=0; collect_group_count<=0;
                        end else begin
                            collect_width0<=max_width0; collect_width1<=max_width1;
                            collect_group_count<=new_group_count;
                        end
                    end
                    collect_count<=collect_count+1'b1;
                    if(end_input_block) begin
                        collect_packet_last<=s_tlast;
                        if(s_tlast) packet_pending<=1;
                        collect_state<=1; select_index<=0;
                        best_cost0<=27'h7ffffff; best_cost1<=27'h7ffffff; best_k0<=0; best_k1<=0;
                    end
                end
                1:begin
                    if(costs0[select_index]<best_cost0) begin best_cost0<=costs0[select_index]; best_k0<=select_index; end
                    if(costs1[select_index]<best_cost1) begin best_cost1<=costs1[select_index]; best_k1<=select_index; end
                    if(select_index==16) collect_state<=2; else select_index<=select_index+1'b1;
                end
                2:begin
                    selected_mode<=0; selected_k<=0; selected_length<={collect_count,1'b0};
                    candidate_index<=0; collect_state<=3;
                end
                3:begin
                    if(candidate_length<selected_length) begin
                        selected_mode<=candidate_mode; selected_k<=candidate_k; selected_length<=candidate_length[11:0];
                    end
                    if(candidate_index==3) collect_state<=4; else candidate_index<=candidate_index+1'b1;
                end
                4:begin
                    bank_count[fill_bank]<=collect_count; bank_crc[fill_bank]<=~collect_crc;
                    bank_channel[fill_bank]<=collect_channel; bank_sequence[fill_bank]<=collect_sequence;
                    bank_mode[fill_bank]<=selected_mode; bank_k[fill_bank]<=selected_k;
                    bank_length[fill_bank]<=selected_length; bank_packet_last[fill_bank]<=collect_packet_last;
                    bank_ready[fill_bank]<=1; fill_bank<=!fill_bank; collect_state<=0;
                    collect_count<=0; collect_crc<=32'hffffffff; previous_sample<=0; previous_previous<=0;
                    group_cost0<=0; group_cost1<=0; collect_group_count<=0; collect_width0<=0; collect_width1<=0;
                    previous_width_valid<=0; previous_width0<=0; previous_width1<=0;
                    for(i=0;i<17;i=i+1) begin costs0[i]<=0; costs1[i]<=0; end
                end
            endcase
            if(state==WAIT_BANK && bank_ready[read_bank]) bank_ready[read_bank]<=0;
        end
    end
    function automatic [7:0] header_byte(input [4:0] position);
        reg [31:0] final_metadata_crc;
        begin
            final_metadata_crc=~metadata_crc_state;
            case(position)
                0:header_byte=8'h42; 1:header_byte=8'h53; 2:header_byte=8'h43; 3:header_byte=8'h31;
                4:header_byte=1; 5:header_byte={6'd0,channel}; 6:header_byte={5'd0,mode};
                7:header_byte={3'd0,rice_k};
                8:header_byte=sequence_number[7:0]; 9:header_byte=sequence_number[15:8];
                10:header_byte=sequence_number[23:16]; 11:header_byte=sequence_number[31:24];
                12:header_byte=count[7:0]; 13:header_byte={5'd0,count[10:8]};
                14:header_byte=payload_length[7:0]; 15:header_byte={4'd0,payload_length[11:8]};
                16:header_byte=sample_crc[7:0]; 17:header_byte=sample_crc[15:8];
                18:header_byte=sample_crc[23:16]; 19:header_byte=sample_crc[31:24];
                20:header_byte=final_metadata_crc[7:0]; 21:header_byte=final_metadata_crc[15:8];
                22:header_byte=final_metadata_crc[23:16]; 23:header_byte=final_metadata_crc[31:24];
                default:header_byte=0;
            endcase
        end
    endfunction
    wire trend_mode=(mode==2 || mode==4);
    wire group_mode=(mode==3 || mode==4);
    wire signed [16:0] stored_error=residual(sample_read,encoder_previous,encoder_previous_previous,
                                              trend_mode,sample_index>=2);
    wire [4:0] stored_width=signed_width(stored_error);
    wire [4:0] final_group_width=stored_width>group_width ? stored_width : group_width;
    wire read_enable=(state==START) || (state==FETCH) || (state==RAW && raw_position[0] && output_slot);
    wire [9:0] read_address=state==START ? 10'd0 : state==FETCH ? sample_index[9:0] : raw_position[10:1]+10'd1;
    always @(posedge clk) begin
        if(resetn && s_tvalid && s_tready) samples[{fill_bank,collect_count[9:0]}]<=s_tdata;
        if(resetn && read_enable) sample_read<=samples[{read_bank,read_address}];
    end

    wire [3:0] free_bits=4'd8-{1'b0,bits_used};
    reg [3:0] chunk_length,take;
    reg [7:0] chunk_bits;
    reg [16:0] quotient_after;
    reg [4:0] remainder_after;
    reg chunk_finished,chunk_remainder,group_done;
    always @* begin
        chunk_length=0; take=0; chunk_bits=0;
        quotient_after=quotient; remainder_after=remainder_left;
        chunk_finished=0; chunk_remainder=(state==REMAINDER); group_done=0;
        if(state==UNARY) begin
            if(quotient>=free_bits) begin chunk_length=free_bits; quotient_after=quotient-free_bits; end
            else begin
                take=free_bits-quotient[3:0]-1'b1;
                if(rice_k<take) take=rice_k[3:0];
                chunk_length=quotient[3:0]+1'b1+take;
                chunk_bits=(8'd1<<take)|((remainder_value>>(rice_k-take)) & (8'hff>>(4'd8-take)));
                quotient_after=0; remainder_after=rice_k-take;
                chunk_finished=(rice_k==take); chunk_remainder=!chunk_finished;
            end
        end else if(state==REMAINDER) begin
            take=free_bits; if(remainder_left<take) take=remainder_left[3:0];
            chunk_length=take;
            chunk_bits=(remainder_value>>(remainder_left-take)) & (8'hff>>(4'd8-take));
            remainder_after=remainder_left-take; chunk_finished=(remainder_left==take);
        end else if(state==FIELD) begin
            take=free_bits; if(field_left<take) take=field_left[3:0];
            chunk_length=take;
            chunk_bits=(field_value>>(field_left-take)) & (8'hff>>(4'd8-take));
            chunk_finished=(field_left==take);
            group_done=chunk_finished && (field_header ? group_width==0 : group_emit_index+3'd1==group_length);
        end
    end
    wire [3:0] next_bits_used={1'b0,bits_used}+chunk_length;
    wire [7:0] next_accumulator=(bit_accumulator<<chunk_length)|chunk_bits;
    always @(posedge clk) begin
        if(!resetn) begin
            state<=WAIT_BANK; read_bank<=0;
            count<=0; sample_index<=1; payload_length<=0; raw_position<=0;
            channel<=0; sequence_number<=0; sample_crc<=0; metadata_crc_state<=32'hffffffff;
            mode<=0; rice_k<=0; header_position<=0; encoder_packet_last<=0; first_high<=0;
            encoder_previous<=0; encoder_previous_previous<=0;
            mapped_value<=0; quotient<=0; remainder_value<=0; remainder_left<=0;
            bit_accumulator<=0; bits_used<=0; m_tdata<=0; m_tvalid<=0; m_tlast<=0;
            group_load_index<=0; group_emit_index<=0; group_length<=0; group_width<=0;
            encoder_previous_width<=0; encoder_previous_width_valid<=0; group_final<=0;
            field_header<=0; field_value<=0; field_left<=0;
        end else begin
            if(m_tvalid && m_tready) begin
                m_tvalid<=0;
                if(m_tlast) begin state<=WAIT_BANK; read_bank<=!read_bank; end
            end
            case(state)
                WAIT_BANK:if(bank_ready[read_bank]) begin
                    count<=bank_count[read_bank]; sample_crc<=bank_crc[read_bank];
                    channel<=bank_channel[read_bank]; sequence_number<=bank_sequence[read_bank];
                    mode<=bank_mode[read_bank]; rice_k<=bank_k[read_bank]; payload_length<=bank_length[read_bank];
                    encoder_packet_last<=bank_packet_last[read_bank]; state<=START;
                end
                START:begin
                    header_position<=0; raw_position<=0; first_high<=0; bits_used<=0; bit_accumulator<=0;
                    sample_index<=1; encoder_previous<=0; encoder_previous_previous<=0;
                    metadata_crc_state<=32'hffffffff; encoder_previous_width_valid<=0;
                    group_load_index<=0; group_emit_index<=0; group_width<=0; state<=HEADER;
                end
                HEADER:if(output_slot) begin
                    m_tdata<=header_byte(header_position); m_tvalid<=1; m_tlast<=0;
                    if(header_position<20) metadata_crc_state<=crc_byte(metadata_crc_state,header_byte(header_position));
                    if(header_position==23) state<=mode==0 ? RAW : FIRST;
                    else header_position<=header_position+1'b1;
                end
                RAW:if(output_slot) begin
                    m_tdata<=raw_position[0] ? sample_read[15:8] : sample_read[7:0]; m_tvalid<=1;
                    m_tlast<=raw_position==payload_length-1'b1;
                    if(raw_position==payload_length-1'b1) state<=WAIT_LAST;
                    else raw_position<=raw_position+1'b1;
                end
                FIRST:if(output_slot) begin
                    m_tdata<=first_high ? sample_read[15:8] : sample_read[7:0]; m_tvalid<=1; m_tlast<=0;
                    if(first_high) begin encoder_previous<=sample_read; state<=FETCH; end
                    else first_high<=1;
                end
                FETCH:state<=DIFFERENCE;
                DIFFERENCE:begin
                    encoder_previous_previous<=encoder_previous; encoder_previous<=sample_read;
                    if(group_mode) begin
                        group_errors[group_load_index]<=stored_error; group_width<=final_group_width;
                        sample_index<=sample_index+1'b1;
                        if(group_load_index==3 || sample_index==count-1'b1) begin
                            group_length<={1'b0,group_load_index}+1'b1;
                            group_final<=sample_index==count-1'b1; state<=GROUP_HEADER;
                        end else begin group_load_index<=group_load_index+1'b1; state<=FETCH; end
                    end else begin mapped_value<=zigzag(stored_error); state<=RICE_START; end
                end
                RICE_START:begin
                    quotient<=mapped_value>>rice_k; remainder_value<=mapped_value; remainder_left<=rice_k; state<=UNARY;
                end
                UNARY,REMAINDER:if(next_bits_used!=8 || output_slot) begin
                    bit_accumulator<=next_accumulator; bits_used<=next_bits_used[2:0];
                    quotient<=quotient_after; remainder_left<=remainder_after;
                    if(next_bits_used==8) begin
                        m_tdata<=next_accumulator; m_tvalid<=1;
                        m_tlast<=chunk_finished && sample_index==count-1'b1;
                    end
                    if(chunk_remainder) state<=REMAINDER;
                    if(chunk_finished) begin
                        if(sample_index==count-1'b1) state<=next_bits_used==8 ? WAIT_LAST : PAD;
                        else begin sample_index<=sample_index+1'b1; state<=FETCH; end
                    end
                end
                GROUP_HEADER:begin
                    field_header<=1;
                    if(!encoder_previous_width_valid) begin field_value<={12'd0,group_width}; field_left<=5; end
                    else if(group_width==encoder_previous_width) begin field_value<=1; field_left<=2; end
                    else if(group_width+6'd1==encoder_previous_width) begin field_value<=0; field_left<=2; end
                    else if(encoder_previous_width+6'd1==group_width) begin field_value<=2; field_left<=2; end
                    else begin field_value<={10'd0,2'b11,group_width}; field_left<=7; end
                    encoder_previous_width<=group_width; encoder_previous_width_valid<=1; state<=FIELD;
                end
                GROUP_SAMPLE:begin
                    field_header<=0; field_value<=group_errors[group_emit_index]; field_left<=group_width; state<=FIELD;
                end
                FIELD:if(next_bits_used!=8 || output_slot) begin
                    bit_accumulator<=next_accumulator; bits_used<=next_bits_used[2:0]; field_left<=field_left-take;
                    if(next_bits_used==8) begin
                        m_tdata<=next_accumulator; m_tvalid<=1; m_tlast<=group_done && group_final;
                    end
                    if(chunk_finished) begin
                        if(group_done) begin
                            if(group_final) state<=next_bits_used==8 ? WAIT_LAST : PAD;
                            else begin group_load_index<=0; group_width<=0; state<=FETCH; end
                        end else if(field_header) begin group_emit_index<=0; state<=GROUP_SAMPLE; end
                        else begin group_emit_index<=group_emit_index+1'b1; state<=GROUP_SAMPLE; end
                    end
                end
                PAD:if(output_slot) begin
                    m_tdata<=bit_accumulator<<(4'd8-{1'b0,bits_used}); m_tvalid<=1; m_tlast<=1; state<=WAIT_LAST;
                end
                WAIT_LAST:begin end
                default:state<=WAIT_BANK;
            endcase
        end
    end
endmodule
