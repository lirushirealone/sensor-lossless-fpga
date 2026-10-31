`timescale 1ns / 1ps
// First, independently verified stage of a future byte-serial DEFLATE path.
// One int16 block at a time; first residual is the sample's exact bit pattern.
// Backpressure is lossless. Checksums describe raw samples / residual bytes,
// respectively. This is not a compressor, DMA wrapper, or deployed overlay.
module delta_checksum_frontend #(
    parameter integer MAX_SAMPLES = 8192
) (
    input wire clk, resetn,
    input wire [15:0] s_data,
    input wire s_valid, s_last,
    output wire s_ready,
    output wire [7:0] m_data,
    output wire m_valid, m_last,
    input wire m_ready,
    output reg done,
    output reg protocol_error,
    output reg [13:0] sample_count,
    output reg [31:0] raw_crc32,
    output reg [31:0] residual_adler32
);
    reg in_block, pending, high_byte, pending_last;
    reg [15:0] previous_sample, residual;
    reg [31:0] crc_state;
    reg [15:0] adler_a, adler_b;
    wire [15:0] difference = in_block ? s_data-previous_sample : s_data;
    assign s_ready = resetn && !protocol_error && !pending;
    assign m_valid = resetn && !protocol_error && pending;
    assign m_data = high_byte ? residual[15:8] : residual[7:0];
    assign m_last = pending_last && high_byte;

    function automatic [31:0] crc_byte(input [31:0] state, input [7:0] data);
        reg [31:0] x;
        integer i;
        begin
            x = state ^ {24'd0, data};
            for(i=0;i<8;i=i+1)
                x = x[0] ? (x>>1)^32'hedb88320 : x>>1;
            crc_byte = x;
        end
    endfunction

    function automatic [15:0] mod_adler(input [16:0] value);
        begin mod_adler = value >= 17'd65521 ? value-17'd65521 : value; end
    endfunction

    wire [31:0] initial_crc = in_block ? crc_state : 32'hffffffff;
    wire [31:0] next_crc = crc_byte(crc_byte(initial_crc,s_data[7:0]),s_data[15:8]);
    wire [15:0] initial_a = in_block ? adler_a : 16'd1;
    wire [15:0] initial_b = in_block ? adler_b : 16'd0;
    wire [15:0] next_a1 = mod_adler({1'b0,initial_a}+{9'd0,difference[7:0]});
    wire [15:0] next_b1 = mod_adler({1'b0,initial_b}+{1'b0,next_a1});
    wire [15:0] next_a2 = mod_adler({1'b0,next_a1}+{9'd0,difference[15:8]});
    wire [15:0] next_b2 = mod_adler({1'b0,next_b1}+{1'b0,next_a2});

    always @(posedge clk) begin
        if(!resetn) begin
            in_block<=0; pending<=0; high_byte<=0; pending_last<=0;
            previous_sample<=0; residual<=0;
            crc_state<=32'hffffffff; adler_a<=1; adler_b<=0;
            sample_count<=0; raw_crc32<=0; residual_adler32<=1;
            done<=0; protocol_error<=0;
        end else begin
            done<=0;
            if(s_valid && s_ready) begin
                // Without a block terminator at the configured bound, stop
                // accepting/outputting data until an explicit reset.
                if((in_block ? sample_count : 14'd0)==MAX_SAMPLES-1 && !s_last) begin
                    protocol_error<=1;
                end else begin
                    previous_sample<=s_data;
                    residual<=difference;
                    pending<=1; high_byte<=0; pending_last<=s_last;
                    sample_count<=in_block ? sample_count+1'b1 : 14'd1;
                    crc_state<=next_crc; raw_crc32<=~next_crc;
                    adler_a<=next_a2; adler_b<=next_b2;
                    residual_adler32<={next_b2,next_a2};
                    in_block<=1;
                end
            end
            if(m_valid && m_ready) begin
                if(!high_byte) high_byte<=1;
                else begin
                    pending<=0; high_byte<=0;
                    if(pending_last) begin in_block<=0; done<=1; end
                end
            end
        end
    end
endmodule
