// Create Thumb functions at addresses listed (hex, one per line) in a file, then re-analyze.
// @category Analysis
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import java.nio.file.*;
public class SeedFuncs extends GhidraScript {
  public void run() throws Exception {
    int n=0;
    var ctx=currentProgram.getProgramContext(); var tm=ctx.getRegister("TMode");
    for (String s: Files.readAllLines(Paths.get(getScriptArgs()[0]))) {
      s=s.trim(); if (s.isEmpty()) continue;
      Address a=toAddr(Long.parseLong(s,16));
      if (getInstructionAt(a)==null) { try { ctx.setValue(tm,a,a,java.math.BigInteger.ONE);} catch(Exception e){} disassemble(a); }
      if (getFunctionContaining(a)==null || !getFunctionContaining(a).getEntryPoint().equals(a)) { if (getFunctionAt(a)==null && createFunction(a,null)!=null) n++; }
    }
    println("seeded "+n); analyzeAll(currentProgram);
  }
}
