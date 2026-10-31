`timescale 1ns / 1ps
// Block-local zlib-level6 lazy matching: hash15, chain128/32, nice128,
// max_lazy16, good8, MAX_MATCH258, discard length3 when distance>4096.
// Byte count <=16384 means no window slide. Position0 is the zlib NIL value.
// Token: literal [7:0], or 0x80000000 | (length<<16) | distance.
module lz77_matcher(
    input wire clk,resetn,
    input wire [7:0] s_data,input wire s_valid,s_last,output wire s_ready,
    output reg [31:0] m_data,output wire m_valid,output reg m_last,input wire m_ready,
    output reg done,protocol_error,
    output reg [14:0] byte_count,token_count,
    output reg [31:0] compute_cycles,search_probes
);
    localparam COLLECT=0,ZERO_PAD=1,CLEAR_HEAD=2,BEGIN_POS=3,
        HASH0=4,HASH1=5,HASH2=6,HEAD_READ=7,HEAD_CAPTURE=8,
        COMPARE_READ=9,COMPARE=10,DECIDE=11,LITERAL_READ=12,LITERAL_CAPTURE=13,
        OUTPUT_TOKEN=14,SKIP_BEGIN=15,FINAL_LENGTH=16;
    reg [4:0] state,after_output;
    // The original 14-bit position plus four tag bits fits one 18-bit word.
    // Epoch zero is invalid. Tags are initialized by an explicit sweep after
    // EVERY reset and before reusing epoch1; no RAM power-up assumption.
    reg [3:0] epoch;
    reg need_clear;
    (* ram_style="block" *) reg [17:0] heads[0:32767];
    (* ram_style="block" *) reg [13:0] previous[0:16383];
    wire [7:0] data_a;
    wire [63:0] bank_vector_a,bank_vector_b;
    reg [2:0] offset_a,offset_b;
    wire [127:0] rotated_a={bank_vector_a,bank_vector_a}>>(8*offset_a);
    wire [127:0] rotated_b={bank_vector_b,bank_vector_b}>>(8*offset_b);
    wire [63:0] window_a=rotated_a[63:0],window_b=rotated_b[63:0];
    assign data_a=window_a[7:0];
    reg [17:0] head_entry_q;
    wire [13:0] head_q=head_entry_q[17:14]==epoch ? head_entry_q[13:0] : 14'd0;
    reg [13:0] previous_q;
    reg [14:0] collect_count,pos,skip_pos,target_pos,clear_index,hash_value;
    reg [8:0] pad_index,match_length,prev_length,best_length,compare_index,skip_left;
    reg [13:0] match_start,prev_match,candidate;
    reg [7:0] budget;
    reg pending,skip_mode;
    reg [14:0] emitted_bytes;
    reg [8:0] emit_length;
    wire [14:0] work_pos=skip_mode ? skip_pos : pos;
    wire [14:0] lookahead=byte_count-pos;
    wire [8:0] nice_length=lookahead<128 ? lookahead[8:0] : 9'd128;
    // Parallel equality plus leading-prefix priority encoder. The compare
    // index advances by8 from0, hence only index256 needs MAX_MATCH clipping.
    wire [7:0] equal_bytes;
    genvar eq_lane;
    generate for(eq_lane=0;eq_lane<8;eq_lane=eq_lane+1)begin: equality
        assign equal_bytes[eq_lane]=window_a[8*eq_lane+:8]==window_b[8*eq_lane+:8];
    end endgenerate
    reg [3:0] raw_prefix;
    always @* begin
        casez(equal_bytes)
            8'b???????0:raw_prefix=0;
            8'b??????01:raw_prefix=1;
            8'b?????011:raw_prefix=2;
            8'b????0111:raw_prefix=3;
            8'b???01111:raw_prefix=4;
            8'b??011111:raw_prefix=5;
            8'b?0111111:raw_prefix=6;
            8'b01111111:raw_prefix=7;
            default:raw_prefix=8;
        endcase
    end
    wire [3:0] prefix_bytes=compare_index==256 && raw_prefix>2 ? 4'd2 : raw_prefix;
    wire [8:0] candidate_length=compare_index+prefix_bytes;
    wire continue_window=compare_index<256 && (&equal_bytes);
    wire improvement=candidate_length>best_length;
    wire [8:0] final_best=best_length;
    wire [13:0] final_start=match_start;
    wire [8:0] bounded_length={6'd0,final_best}>lookahead ? lookahead[8:0] : final_best;
    wire [8:0] filtered_length=(bounded_length==3 && pos-{1'b0,final_start}>4096) ? 9'd2 : bounded_length;
    reg [14:0] address_a,address_b;
    always @* begin
        address_a=0;address_b=0;
        case(state)
            HASH0:address_a=work_pos;
            HEAD_CAPTURE:begin address_a=pos;address_b={1'b0,head_q};end
            COMPARE_READ:begin address_a=pos+compare_index;address_b={1'b0,candidate}+compare_index;end
            COMPARE:begin
                // The registered current windows remain available while the
                // RAMs fetch the next chunk or next chain candidate.
                if(continue_window)begin address_a=pos+compare_index+15'd8;address_b={1'b0,candidate}+compare_index+15'd8;end
                else begin address_a=pos;address_b={1'b0,previous_q};end
            end
            LITERAL_READ:address_a=pos-1'b1;
            default:begin end
        endcase
    end
    wire [14:0] new_hash=({7'd0,window_a[7:0]}<<10)^({7'd0,window_a[15:8]}<<5)^{7'd0,window_a[23:16]};
    assign s_ready=resetn && state==COLLECT && !protocol_error;
    assign m_valid=resetn && state==OUTPUT_TOKEN && !protocol_error;
    // Explicit single write muxes and read/write address sharing make the
    // two physical RAM ports unambiguous to the synthesis mapper.
    wire data_write=resetn && ((state==COLLECT && s_valid && s_ready) || state==ZERO_PAD);
    wire [14:0] data_port_a=state==COLLECT ? collect_count : state==ZERO_PAD ? byte_count+pad_index : address_a;
    wire [7:0] data_write_value=state==ZERO_PAD ? 8'd0 : s_data;
    wire head_write=resetn && (state==CLEAR_HEAD || state==HEAD_CAPTURE);
    wire [14:0] head_write_address=state==CLEAR_HEAD ? clear_index : hash_value;
    wire [17:0] head_write_value=state==CLEAR_HEAD ? 18'd0 : {epoch,work_pos[13:0]};
    wire [14:0] head_read_address=state==HASH1 ? new_hash : hash_value;
    wire [13:0] previous_read_address=state==HEAD_CAPTURE ? head_q :
        (state==COMPARE && !continue_window) ? previous_q : candidate;
    genvar bank;
    generate for(bank=0;bank<8;bank=bank+1)begin: byte_bank
        // Each bank supplies one byte to each independently aligned window.
        // Port A shares its address for writes and reads, allowing true BRAM.
        (* ram_style="block" *) reg [7:0] memory[0:2081];
        reg [7:0] q_a,q_b;
        // Complete address arithmetic BEFORE the RAM-dependent decision.
        // Aligned increments keep the lane offset unchanged. Only the final
        // mux depends on equal_bytes, cutting the read->prefix->add feedback.
        wire [11:0] word_a_general=data_port_a[14:3]+(bank<data_port_a[2:0]);
        wire [11:0] word_b_general=address_b[14:3]+(bank<address_b[2:0]);
        wire [11:0] word_a_next=pos[14:3]+compare_index[8:3]+12'd1+(bank<pos[2:0]);
        wire [11:0] word_b_next={1'b0,candidate[13:3]}+compare_index[8:3]+12'd1+(bank<candidate[2:0]);
        wire [11:0] word_a_chain=pos[14:3]+(bank<pos[2:0]);
        wire [11:0] word_b_chain={1'b0,previous_q[13:3]}+(bank<previous_q[2:0]);
        wire [11:0] word_a=state==COMPARE ? (continue_window?word_a_next:word_a_chain) : word_a_general;
        wire [11:0] word_b=state==COMPARE ? (continue_window?word_b_next:word_b_chain) : word_b_general;
        always @(posedge clk)begin
            if(data_write && data_port_a[2:0]==bank)memory[word_a]<=data_write_value;
            q_a<=memory[word_a];q_b<=memory[word_b];
        end
        assign bank_vector_a[bank*8+:8]=q_a;
        assign bank_vector_b[bank*8+:8]=q_b;
    end endgenerate
    always @(posedge clk) begin
        offset_a<=data_port_a[2:0];offset_b<=address_b[2:0];
        if(head_write)heads[head_write_address]<=head_write_value;
        head_entry_q<=heads[head_read_address];
        previous_q<=previous[previous_read_address];
        if(resetn && state==HEAD_CAPTURE)
            previous[work_pos[13:0]]<=head_q;
    end
    always @(posedge clk) begin
        if(!resetn) begin
            state<=COLLECT;after_output<=BEGIN_POS;done<=0;protocol_error<=0;epoch<=0;need_clear<=1;
            byte_count<=0;token_count<=0;compute_cycles<=0;search_probes<=0;
            collect_count<=0;pos<=0;skip_pos<=0;target_pos<=0;clear_index<=0;hash_value<=0;
            pad_index<=0;match_length<=2;prev_length<=2;best_length<=2;compare_index<=0;skip_left<=0;
            match_start<=0;prev_match<=0;candidate<=0;budget<=0;
            pending<=0;skip_mode<=0;emitted_bytes<=0;emit_length<=0;
            m_data<=0;m_last<=0;
        end else begin
            done<=0;
            if(state!=COLLECT && state!=OUTPUT_TOKEN && !protocol_error)
                compute_cycles<=compute_cycles+1'b1;
            if(!protocol_error) case(state)
                COLLECT:if(s_valid && s_ready) begin
                    if(collect_count==16383 && !s_last)protocol_error<=1;
                    else begin
                        if(collect_count==0) begin
                            token_count<=0;compute_cycles<=0;search_probes<=0;
                            if(epoch==15)begin epoch<=1;need_clear<=1;end
                            else epoch<=epoch+1'b1;
                        end
                        collect_count<=collect_count+1'b1;
                        if(s_last) begin
                            byte_count<=collect_count+1'b1;pad_index<=0;state<=ZERO_PAD;
                            pos<=0;emitted_bytes<=0;pending<=0;skip_mode<=0;match_length<=2;match_start<=0;
                        end
                    end
                end
                ZERO_PAD:begin
                    if(pad_index==257)begin
                        if(need_clear)begin clear_index<=0;state<=CLEAR_HEAD;end
                        else state<=BEGIN_POS;
                    end
                    else pad_index<=pad_index+1'b1;
                end
                CLEAR_HEAD:begin
                    if(clear_index==32767)begin need_clear<=0;state<=BEGIN_POS;end
                    else clear_index<=clear_index+1'b1;
                end
                BEGIN_POS:begin
                    if(pos>=byte_count) begin
                        if(!pending)protocol_error<=1;
                        else begin after_output<=BEGIN_POS;state<=LITERAL_READ;end
                    end else begin
                        prev_length<=match_length;prev_match<=match_start;match_length<=2;
                        if(lookahead>=3)state<=HASH0;
                        else state<=DECIDE;
                    end
                end
                HASH0:state<=HASH1;
                HASH1:begin hash_value<=new_hash;state<=HEAD_CAPTURE;end
                HASH2:begin hash_value<=new_hash;state<=HEAD_READ;end
                HEAD_READ:state<=HEAD_CAPTURE;
                HEAD_CAPTURE:begin
                    if(skip_mode)begin
                        skip_left<=skip_left-1'b1;skip_pos<=skip_pos+1'b1;
                        if(skip_left==1)begin pos<=target_pos;skip_mode<=0;state<=BEGIN_POS;end
                        else state<=SKIP_BEGIN;
                    end else if(head_q!=0 && prev_length<16) begin
                        candidate<=head_q;best_length<=prev_length;compare_index<=0;
                        budget<=prev_length>=8 ? 8'd32 : 8'd128;
                        state<=COMPARE;search_probes<=search_probes+1'b1;
                    end else state<=DECIDE;
                end
                COMPARE_READ:state<=COMPARE;
                COMPARE:begin
                    if(continue_window)begin compare_index<=compare_index+9'd8;state<=COMPARE;end
                    else begin
                        if(improvement)begin best_length<=candidate_length;match_start<=candidate;end
                        if((improvement && candidate_length>=nice_length) || previous_q==0 || budget==1)begin
                            state<=FINAL_LENGTH;
                        end else begin
                            candidate<=previous_q;budget<=budget-1'b1;compare_index<=0;
                            search_probes<=search_probes+1'b1;state<=COMPARE;
                        end
                    end
                end
                // Registers best_length/match_start cut the RAM-to-length path.
                FINAL_LENGTH:begin match_length<=filtered_length;state<=DECIDE;end
                DECIDE:begin
                    if(prev_length>=3 && match_length<=prev_length)begin
                        if(({1'b0,prev_match}+1'b1)>=pos)protocol_error<=1;
                        else begin
                            m_data<=32'h80000000|({23'd0,prev_length}<<16)|({17'd0,pos}-1'b1-{18'd0,prev_match});
                            emit_length<=prev_length;m_last<=emitted_bytes+prev_length==byte_count;
                            skip_left<=prev_length-2'd2;skip_pos<=pos+1'b1;target_pos<=pos+prev_length-1'b1;
                            pending<=0;match_length<=2;skip_mode<=1;after_output<=SKIP_BEGIN;state<=OUTPUT_TOKEN;
                        end
                    end else if(pending)begin after_output<=BEGIN_POS;state<=LITERAL_READ;end
                    else begin pending<=1;pos<=pos+1'b1;state<=BEGIN_POS;end
                end
                LITERAL_READ:state<=LITERAL_CAPTURE;
                LITERAL_CAPTURE:begin
                    m_data<={24'd0,data_a};emit_length<=1;m_last<=emitted_bytes+1'b1==byte_count;
                    pos<=pos+1'b1;state<=OUTPUT_TOKEN;
                end
                OUTPUT_TOKEN:if(m_ready)begin
                    token_count<=token_count+1'b1;emitted_bytes<=emitted_bytes+emit_length;
                    if(m_last)begin state<=COLLECT;collect_count<=0;done<=1;end
                    else state<=after_output;
                end
                SKIP_BEGIN:begin
                    if(skip_pos+2'd2<byte_count)state<=HASH0;
                    else begin
                        skip_left<=skip_left-1'b1;skip_pos<=skip_pos+1'b1;
                        if(skip_left==1)begin pos<=target_pos;skip_mode<=0;state<=BEGIN_POS;end
                    end
                end
                default:protocol_error<=1;
            endcase
        end
    end
endmodule
