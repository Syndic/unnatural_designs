// Headless post-script: decompile every function to one C file (path = first script arg).
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import java.io.PrintWriter;

public class DecompileAll extends GhidraScript {
    @Override
    public void run() throws Exception {
        String out = getScriptArgs().length > 0 ? getScriptArgs()[0] : "decomp.c";
        DecompInterface d = new DecompInterface();
        d.openProgram(currentProgram);
        int n = 0;
        try (PrintWriter w = new PrintWriter(out)) {
            for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
                DecompileResults r = d.decompileFunction(f, 60, monitor);
                w.println("// ---- " + f.getName() + " @ " + f.getEntryPoint());
                w.println(r.decompileCompleted()
                        ? r.getDecompiledFunction().getC()
                        : "// decompile failed");
                n++;
            }
        }
        println("decompiled " + n + " functions to " + out);
    }
}
