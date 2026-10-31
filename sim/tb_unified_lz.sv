`timescale 1ns / 1ps
module tb_unified_lz(output reg done=0);
    reg aclk=0;always #7.5 aclk=~aclk;
    reg aresetn=0;
    reg [31:0] s_axis_tdata=0;reg s_axis_tvalid=0,s_axis_tlast=0,m_axis_tready=0;
    wire s_axis_tready,m_axis_tvalid,m_axis_tlast;wire [31:0] m_axis_tdata;wire [3:0] m_axis_tkeep;
    wire [31:0] stats_total_cycles,stats_collect_cycles,stats_process_cycles,stats_output_stalls;
    wire [31:0] stats_input_samples,stats_output_bytes,stats_sequence,stats_input_gaps,stats_matcher_work_cycles,stats_search_probes;
    lz77_axis dut(.*);
    reg [32:0] inputs[0:999999];
    integer fd,wirefd,statsfd,ni,np,ii=0,packets=0,seed=0,cycle=0,j;
    integer packet_bytes=0,first_clock=0,stalls=0,total_stalls=0,input_gaps=0,reset_cases=0,bad_cases=0;
    reg running=0,held_valid=0;reg [36:0] held;reg [31:0] rng=17;
    function automatic [31:0] step(input [31:0] x);
        reg [31:0] y;begin y=x^(x<<13);y=y^(y>>17);step=y^(y<<5);end
    endfunction
    task automatic reset;
        begin
            @(negedge aclk);aresetn=0;s_axis_tvalid=0;m_axis_tready=0;
            repeat(3)@(negedge aclk);
            if(m_axis_tvalid||dut.error||dut.active||stats_sequence||dut.block_core.sequence_number)$fatal(1,"Reset failed");
            aresetn=1;reset_cases=reset_cases+1;
        end
    endtask
    task automatic send(input [31:0] word,input last);
        begin
            @(negedge aclk);s_axis_tdata=word;s_axis_tlast=last;s_axis_tvalid=1;
            @(posedge aclk);while(!s_axis_tready)@(posedge aclk);
            @(negedge aclk);s_axis_tvalid=0;
        end
    endtask
    task automatic check_error;
        begin repeat(4)@(negedge aclk);if(!dut.error||s_axis_tready||m_axis_tvalid)$fatal(1,"Malformed input accepted");bad_cases=bad_cases+1;reset();end
    endtask
    initial begin
        if(!$value$plusargs("SEED=%d",seed))seed=0;
        rng=seed==0?32'h7020:seed;
        fd=$fopen("lz-counts.txt","r");if($fscanf(fd,"%d %d",ni,np)!=2)$fatal(1,"Missing counts");$fclose(fd);
        $readmemh("lz-input.hex",inputs,0,ni-1);wirefd=$fopen("lz-actual.hex","w");statsfd=$fopen("lz-stats.txt","w");reset();
        send(32'h4c420000,0);check_error();send(32'h4c420003,0);check_error();send(32'h4c420001,1);check_error();
        send(32'h4c430001,0);check_error();send(32'h4c420001,0);send(32'h4c5a0000,0);check_error();
        send(32'h4c420001,0);send(32'h4c5a2001,0);check_error();
        send(32'h4c420001,0);send(32'h4c5a0001,1);check_error();
        send(32'h4c420002,0);send(32'h4c5a0001,0);send(42,1);check_error();
        send(32'h4c420001,0);send(32'h4c5a0001,0);send(42,0);check_error();
        send(32'h4c420001,0);send(32'h4c5a0001,0);send(32'h10001,1);check_error();
        send(32'h4c420002,0);send(32'h4c5a0011,0);send(42,0);reset();
        send(32'h4c420001,0);send(32'h4c5a0001,0);send(42,1);wait(m_axis_tvalid);repeat(7)@(negedge aclk);reset();
        m_axis_tready=1;send(32'h4c420001,0);send(32'h4c5a0001,0);send(42,1);
        wait(dut.state==5);@(negedge aclk);m_axis_tready=0;repeat(7)@(negedge aclk);reset();
        m_axis_tready=1;send(32'h4c420001,0);send(32'h4c5a0001,0);send(42,1);
        wait(dut.state==6);reset();
        m_axis_tready=1;send(32'h4c420001,0);send(32'h4c5a0001,0);send(42,1);
        wait(m_axis_tvalid&&m_axis_tlast);@(negedge aclk);m_axis_tready=0;repeat(7)@(negedge aclk);reset();
        @(negedge aclk);running=1;wait(packets==np);@(negedge aclk);running=0;s_axis_tvalid=0;m_axis_tready=1;
        repeat(5)@(posedge aclk);if(ii!=ni||dut.error||dut.active||!s_axis_tready||m_axis_tvalid)$fatal(1,"Final idle mismatch");
        if(seed!=0&&(!total_stalls||!input_gaps))$fatal(1,"Missing stalls/gaps");
        $fclose(wirefd);$fclose(statsfd);
        $display("LZ77_BATCH2_SIM_PASS seed=%0d batches=%0d words=%0d stalls=%0d gaps=%0d malformed=%0d resets=%0d",seed,np,ni,total_stalls,input_gaps,bad_cases,reset_cases);done=1;
    end
    initial begin #1500000000;$fatal(1,"Global timeout");end
    always @(negedge aclk)if(running)begin
        rng=step(rng);m_axis_tready=seed==0||rng[3:0]>=5;
        if(!s_axis_tvalid||s_axis_tready)begin
            s_axis_tvalid=ii<ni&&(seed==0||rng[7:4]>=3);
            if(ii<ni)begin s_axis_tdata=inputs[ii][31:0];s_axis_tlast=inputs[ii][32];end
        end
    end
    always @(posedge aclk)if(running)begin
        cycle=cycle+1;
        if(held_valid&&(!m_axis_tvalid||{m_axis_tlast,m_axis_tkeep,m_axis_tdata}!==held))$fatal(1,"Output changed under backpressure");
        held_valid=m_axis_tvalid&&!m_axis_tready;held={m_axis_tlast,m_axis_tkeep,m_axis_tdata};
        if(dut.error)$fatal(1,"Valid input caused error");
        if(s_axis_tready&&!s_axis_tvalid)input_gaps=input_gaps+1;
        if(s_axis_tvalid&&s_axis_tready)begin
            ii=ii+1;if(dut.state==0)begin first_clock=cycle;packet_bytes=0;stalls=0;end
        end
        if(m_axis_tvalid&&!m_axis_tready)begin stalls=stalls+1;total_stalls=total_stalls+1;end
        if(m_axis_tvalid&&m_axis_tready)begin
            if(m_axis_tkeep!=15)$fatal(1,"Bad keep");
            $fwrite(wirefd,"%09x\n",{m_axis_tlast,m_axis_tdata});packet_bytes=packet_bytes+4;
            if(m_axis_tlast)begin
                #1;
                if(stats_sequence!=packets+1||stats_total_cycles!=cycle-first_clock+1||stats_output_stalls!=stalls||stats_output_bytes!=packet_bytes)$fatal(1,"Outer counters mismatch");
                $fwrite(statsfd,"%0d %0d %0d %0d %0d %0d %0d %0d %0d %0d\n",stats_total_cycles,stats_collect_cycles,stats_process_cycles,stats_output_stalls,stats_input_samples,stats_output_bytes,stats_sequence,stats_input_gaps,stats_matcher_work_cycles,stats_search_probes);
                packets=packets+1;
            end
        end
    end
endmodule
