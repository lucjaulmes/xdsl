// RUN: XDSL_ROUNDTRIP

builtin.module {
  %clk = "test.op"() : () -> (!seq.clock)
  %wire = "test.op"() : () -> (i1)

  %div_clk = seq.clock_div %clk by 4
  // CHECK:      %div_clk = seq.clock_div %clk by 4

  %mux_clk = seq.clock_mux %wire, %clk, %div_clk
  // CHECK:      %mux_clk = seq.clock_mux %wire, %clk, %div_clk
}
