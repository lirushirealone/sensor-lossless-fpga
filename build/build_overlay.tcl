# Two qualified compute engines in one overlay. Separate50/66.67MHz domains.
set_param general.maxThreads 4
create_project compressor_project project -part xc7z020clg400-1 -force
add_files [list sensor_compressor_1024.sv parallel_axis_1024.sv delta_checksum_frontend.sv lz77_matcher.sv lz77_block_axis.sv lz77_axis.sv]
set_property file_type SystemVerilog [get_files *.sv]
update_compile_order -fileset sources_1
create_bd_design compressor_system
set ps7 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7]
source ps7_preset.tcl
set_property -dict [list CONFIG.PCW_USE_M_AXI_GP0 {1} CONFIG.PCW_USE_S_AXI_HP0 {1} CONFIG.PCW_USE_S_AXI_HP1 {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {66.666667} CONFIG.PCW_EN_CLK1_PORT {1} CONFIG.PCW_FPGA1_PERIPHERAL_FREQMHZ {50} \
    CONFIG.PCW_EN_CLK2_PORT {0} CONFIG.PCW_EN_CLK3_PORT {0} CONFIG.PCW_EN_RST1_PORT {1} CONFIG.PCW_EN_RST2_PORT {0} CONFIG.PCW_EN_RST3_PORT {0} CONFIG.PCW_USE_FABRIC_INTERRUPT {0}] $ps7
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 -config {make_external "FIXED_IO, DDR" apply_board_preset "0"} $ps7
set ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 control_ic]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {12}] $ctrl
connect_bd_intf_net [get_bd_intf_pins ps7/M_AXI_GP0] [get_bd_intf_pins control_ic/S00_AXI]
set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 locked_1]
set_property CONFIG.CONST_VAL {1} $one
for {set d 0} {$d<2} {incr d} {
    set clk [get_bd_pins ps7/FCLK_CLK$d]
    set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 reset_$d]
    connect_bd_net $clk [get_bd_pins reset_$d/slowest_sync_clk]
    connect_bd_net [get_bd_pins locked_1/dout] [get_bd_pins reset_$d/dcm_locked]
    connect_bd_net [get_bd_pins ps7/FCLK_RESET${d}_N] [get_bd_pins reset_$d/ext_reset_in]
    set resetn [get_bd_pins reset_$d/peripheral_aresetn]
    if {$d==0} {
        set dma axi_dma_0;set core compressor_lz;set module lz77_axis;set prefix stats;set offset 0
        set pins [list stats_total_cycles stats_collect_cycles stats_process_cycles stats_output_stalls stats_input_samples stats_output_bytes stats_sequence stats_input_gaps stats_matcher_work_cycles stats_search_probes]
        foreach p {ps7/M_AXI_GP0_ACLK control_ic/ACLK control_ic/S00_ACLK} {connect_bd_net $clk [get_bd_pins $p]}
        foreach p {control_ic/ARESETN control_ic/S00_ARESETN} {connect_bd_net $resetn [get_bd_pins $p]}
    } else {
        set dma axi_dma_rice;set core compressor_rice;set module parallel_axis_1024;set prefix rice_stats;set offset 6
        set pins [list stats_total_cycles stats_collect_cycles stats_process_cycles stats_output_stalls stats_input_samples stats_output_bytes stats_sequence stats_input_gaps stats_busy_lane_cycles stats_max_active_lanes]
    }
    set ip [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 $dma]
    set_property -dict [list CONFIG.c_include_sg {0} CONFIG.c_sg_length_width {18} CONFIG.c_include_mm2s_dre {1} CONFIG.c_include_s2mm_dre {1} CONFIG.c_m_axis_mm2s_tdata_width {32} CONFIG.c_s_axis_s2mm_tdata_width {32} CONFIG.c_m_axi_mm2s_data_width {64} CONFIG.c_m_axi_s2mm_data_width {64}] $ip
    set endpoint [create_bd_cell -type module -reference $module $core]
    if {$d==1} {set_property CONFIG.LANES {4} $endpoint}
    connect_bd_intf_net [get_bd_intf_pins $dma/M_AXIS_MM2S] [get_bd_intf_pins $core/S_AXIS]
    connect_bd_intf_net [get_bd_intf_pins $core/M_AXIS] [get_bd_intf_pins $dma/S_AXIS_S2MM]
    set mem [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 memory_$d]
    set_property -dict [list CONFIG.NUM_SI {2} CONFIG.NUM_MI {1}] $mem
    connect_bd_intf_net [get_bd_intf_pins $dma/M_AXI_MM2S] [get_bd_intf_pins memory_$d/S00_AXI]
    connect_bd_intf_net [get_bd_intf_pins $dma/M_AXI_S2MM] [get_bd_intf_pins memory_$d/S01_AXI]
    connect_bd_intf_net [get_bd_intf_pins memory_$d/M00_AXI] [get_bd_intf_pins ps7/S_AXI_HP$d]
    connect_bd_net $clk [get_bd_pins ps7/S_AXI_HP${d}_ACLK]
    set port [format "M%02d" $offset]
    connect_bd_intf_net [get_bd_intf_pins control_ic/${port}_AXI] [get_bd_intf_pins $dma/S_AXI_LITE]
    foreach p [list $core/aclk $dma/s_axi_lite_aclk $dma/m_axi_mm2s_aclk $dma/m_axi_s2mm_aclk control_ic/${port}_ACLK memory_$d/ACLK memory_$d/S00_ACLK memory_$d/S01_ACLK memory_$d/M00_ACLK] {connect_bd_net $clk [get_bd_pins $p]}
    foreach p [list $core/aresetn $dma/axi_resetn control_ic/${port}_ARESETN memory_$d/ARESETN memory_$d/S00_ARESETN memory_$d/S01_ARESETN memory_$d/M00_ARESETN] {connect_bd_net $resetn [get_bd_pins $p]}
    for {set i 0} {$i<5} {incr i} {
        set name ${prefix}_$i;set bank [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 $name]
        set_property -dict [list CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO_WIDTH {32} CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS {1} CONFIG.C_ALL_INPUTS_2 {1}] $bank
        set port [format "M%02d" [expr {$offset+$i+1}]]
        connect_bd_intf_net [get_bd_intf_pins control_ic/${port}_AXI] [get_bd_intf_pins $name/S_AXI]
        connect_bd_net $clk [get_bd_pins $name/s_axi_aclk] [get_bd_pins control_ic/${port}_ACLK]
        connect_bd_net $resetn [get_bd_pins $name/s_axi_aresetn] [get_bd_pins control_ic/${port}_ARESETN]
        connect_bd_net [get_bd_pins $core/[lindex $pins [expr {2*$i}]]] [get_bd_pins $name/gpio_io_i]
        connect_bd_net [get_bd_pins $core/[lindex $pins [expr {2*$i+1}]]] [get_bd_pins $name/gpio2_io_i]
    }
}
assign_bd_address
validate_bd_design
save_bd_design
generate_target all [get_files compressor_system.bd]
set wrapper [make_wrapper -files [get_files compressor_system.bd] -top]
add_files -norecurse $wrapper
set_property top compressor_system_wrapper [current_fileset]
update_compile_order -fileset sources_1
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis failed"}
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation failed"}
open_run impl_1
report_timing_summary -file implemented-timing.rpt
report_utilization -file implemented-utilization.rpt
report_drc -file implemented-drc.rpt
report_cdc -file implemented-cdc.rpt
set setup_slack [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set hold_slack [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
if {$setup_slack<0 || $hold_slack<0} {error "Routed timing failed: setup $setup_slack hold $hold_slack"}
file mkdir overlay
file copy -force project/compressor_project.runs/impl_1/compressor_system_wrapper.bit overlay/compressor.bit
file copy -force project/compressor_project.gen/sources_1/bd/compressor_system/hw_handoff/compressor_system.hwh overlay/compressor.hwh
puts "UNIFIED_BUILD_PASS setup_slack=$setup_slack hold_slack=$hold_slack"
exit
