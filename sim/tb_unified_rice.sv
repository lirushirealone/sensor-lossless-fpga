`timescale 1ns / 1ps
`ifndef LANES
`define LANES 4
`endif
module tb_unified_rice(output reg done=0);
    reg aclk=0; always #10 aclk=~aclk;
    reg aresetn=0;
    reg [31:0] s_axis_tdata=0;
    reg s_axis_tvalid=0,s_axis_tlast=0;
    wire s_axis_tready;
    wire [31:0] m_axis_tdata;
    wire [3:0] m_axis_tkeep;
    wire m_axis_tvalid,m_axis_tlast;
    reg m_axis_tready=0;
    wire [31:0] stats_total_cycles,stats_collect_cycles,stats_process_cycles;
    wire [31:0] stats_output_stalls,stats_input_samples,stats_output_bytes,stats_sequence,stats_input_gaps;
    wire [31:0] stats_busy_lane_cycles,stats_max_active_lanes;
    parallel_axis_1024 #(.LANES(`LANES)) dut(.*);
    reg [32:0] inputs[0:299999]; reg [7:0] expected[0:599999];
    reg [31:0] lookup_start[0:8191],lookup_length[0:8191];
    reg [7:0] record[0:2071]; reg seen[0:8191];
    integer ni,nb,np,nf,ns,ii=0,oi=0,frames=0,packets=0,cycle=0,fd,outfd,seed=0,lane,j;
    integer pos=0,record_length=0,ch,number,key,start,packet_frames=0,declared=0;
    integer sample_total=0,packet_samples=0,packet_bytes=0,first_input=0,last_input=0,packet_stalls=0;
    integer measured_cycles=0,integral_busy=0,max_busy=0,queue_stalls=0;
    reg is_command=1,running=0,held_valid=0;
    reg [36:0] held;
    reg [31:0] rng=17; reg [1023:0] actual;
    function automatic [31:0] step(input [31:0] x);
        reg [31:0] y; begin y=x^(x<<13); y=y^(y>>17); step=y^(y<<5); end
    endfunction
    task automatic reset;
        begin
            @(negedge aclk); aresetn=0; s_axis_tvalid=0; m_axis_tready=0;
            repeat(3) @(negedge aclk);
            if(dut.queue_ready || dut.packet_active || dut.protocol_error || dut.frame_ready || dut.core_busy || m_axis_tvalid || stats_sequence) $fatal(1,"Reset failed");
            aresetn=1;
        end
    endtask
    task automatic send(input [31:0] value,input last);
        begin
            @(negedge aclk); s_axis_tdata=value; s_axis_tlast=last; s_axis_tvalid=1;
            @(posedge aclk); while(!s_axis_tready) @(posedge aclk);
            @(negedge aclk); s_axis_tvalid=0;
        end
    endtask
    initial begin
        if(!$value$plusargs("SEED=%d",seed)) seed=0;
        rng=seed==0 ? 32'h7020 : seed;
        if(!$value$plusargs("ACTUAL=%s",actual)) actual="rice-actual.hex";
        fd=$fopen("rice-counts.txt","r");
        if($fscanf(fd,"%d %d %d %d %d",ni,nb,np,nf,ns)!=5) $fatal(1,"Missing counts");
        $fclose(fd); $readmemh("rice-input.hex",inputs,0,ni-1); $readmemh("rice-expected.hex",expected,0,nb-1);
        $readmemh("rice-lookup_start.hex",lookup_start); $readmemh("rice-lookup_length.hex",lookup_length);
        for(j=0;j<8192;j=j+1) seen[j]=0;
        outfd=$fopen(actual,"w"); reset();
        send(32'hffffffff,0); repeat(3) @(negedge aclk);
        if(!dut.protocol_error || s_axis_tready) $fatal(1,"Malformed command accepted"); reset();
        send(32'h4d430004,0); repeat(17) send(42,0); reset();
        send(32'h4d430004,0); send(32'h0004002a,0);
        wait(m_axis_tvalid); repeat(20) @(negedge aclk); reset();
        @(negedge aclk); running=1;
        wait(oi==nb);
        @(negedge aclk); running=0; s_axis_tvalid=0; m_axis_tready=1;
        repeat(20) @(posedge aclk);
        if(ii!=ni || frames!=nf || packets!=np || sample_total!=ns || m_axis_tvalid || dut.frame_ready) $fatal(1,"Count mismatch");
        if(max_busy>`LANES || max_busy<`LANES || queue_stalls==0) $fatal(1,"Missing concurrency/queue pressure");
        $fclose(outfd);
        $display("PARALLEL_PASS lanes=%0d seed=%0d packets=%0d frames=%0d samples=%0d bytes=%0d measured_cycles=%0d busy_lane_cycles=%0d max_busy=%0d queue_stalls=%0d",`LANES,seed,packets,frames,sample_total,oi,measured_cycles,integral_busy,max_busy,queue_stalls);
        done=1;
    end
    initial begin #200000000; $fatal(1,"Global timeout"); end
    always @(negedge aclk) if(running) begin
        rng=step(rng);
        m_axis_tready=seed==0 || rng[3:0]>=4;
        if(!s_axis_tvalid || s_axis_tready) begin
            s_axis_tvalid=ii<ni && (seed==0 || rng[7:4]>=2);
            if(ii<ni) begin s_axis_tdata=inputs[ii][31:0]; s_axis_tlast=inputs[ii][32]; end
        end
    end
    always @(posedge aclk) if(running) begin
        cycle=cycle+1;
        if(held_valid && {m_axis_tlast,m_axis_tkeep,m_axis_tdata}!==held) $fatal(1,"Output changed under backpressure");
        held_valid=m_axis_tvalid && !m_axis_tready; held={m_axis_tlast,m_axis_tkeep,m_axis_tdata};
        if(s_axis_tvalid && !s_axis_tready) queue_stalls=queue_stalls+1;
        if(m_axis_tvalid && !m_axis_tready) packet_stalls=packet_stalls+1;
        if(s_axis_tvalid && s_axis_tready) begin
            ii=ii+1;
            if(is_command) begin declared=s_axis_tdata[7:0]; is_command=0; packet_samples=0; packet_bytes=0; packet_frames=0; packet_stalls=0; end
            else begin
                if(packet_samples==0) first_input=cycle;
                packet_samples=packet_samples+1; sample_total=sample_total+1;
                if(s_axis_tlast) begin last_input=cycle; is_command=1; end
            end
        end
        if(m_axis_tvalid && m_axis_tready) begin
            if(!m_axis_tlast && m_axis_tkeep!=15) $fatal(1,"Partial keep before packet end");
            if(m_axis_tkeep!=1 && m_axis_tkeep!=3 && m_axis_tkeep!=7 && m_axis_tkeep!=15) $fatal(1,"Invalid keep");
            for(lane=0;lane<4;lane=lane+1) if(m_axis_tkeep[lane]) begin
                record[pos]=(m_axis_tdata>>(lane*8))&255; pos=pos+1; oi=oi+1; packet_bytes=packet_bytes+1;
                if(pos==16) record_length=24+record[14]+256*record[15];
                if(record_length && pos==record_length) begin
                    ch=record[5]; number=record[8]+256*record[9]+65536*record[10]+16777216*record[11]; key=ch*2048+number;
                    if(ch>3 || number>=2048 || seen[key] || lookup_length[key]!=record_length) $fatal(1,"Unexpected frame");
                    start=lookup_start[key];
                    for(j=0;j<record_length;j=j+1) if(record[j]!==expected[start+j]) $fatal(1,"Byte mismatch channel=%0d seq=%0d byte=%0d actual=%0h expected=%0h",ch,number,j,record[j],expected[start+j]);
                    for(j=0;j<record_length;j=j+1) $fwrite(outfd,"%03x\n",{j==record_length-1,record[j]});
                    seen[key]=1; frames=frames+1; packet_frames=packet_frames+1; pos=0; record_length=0;
                end
            end
            if(m_axis_tlast) begin
                if(pos || packet_frames!=declared) $fatal(1,"Wrong packet framing");
                #1;
                if(stats_total_cycles!=cycle-first_input+1 || stats_collect_cycles!=last_input-first_input+1 || stats_total_cycles!=stats_collect_cycles+stats_process_cycles || stats_input_samples!=packet_samples || stats_output_bytes!=packet_bytes || stats_sequence!=frames || stats_output_stalls!=packet_stalls || stats_collect_cycles!=stats_input_samples+stats_input_gaps) $fatal(1,"Performance snapshot mismatch");
                measured_cycles=measured_cycles+stats_total_cycles; integral_busy=integral_busy+stats_busy_lane_cycles;
                if(stats_max_active_lanes>max_busy) max_busy=stats_max_active_lanes;
                $display("PACKET lanes=%0d seed=%0d index=%0d cycles=%0d samples=%0d bytes=%0d max_busy=%0d",`LANES,seed,packets,stats_total_cycles,packet_samples,packet_bytes,stats_max_active_lanes);
                packets=packets+1;
            end
        end
    end
endmodule
