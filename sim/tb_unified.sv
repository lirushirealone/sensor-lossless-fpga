`timescale 1ns/1ps
module tb_unified;wire lz_done,rice_done;tb_unified_lz lz(lz_done);tb_unified_rice rice(rice_done);initial begin wait(lz_done&&rice_done);#100;$display("UNIFIED_DUAL_CLOCK_PASS");$finish;end endmodule
