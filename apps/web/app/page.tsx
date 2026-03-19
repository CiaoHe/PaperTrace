import { AnalysisForm } from "@/components/analysis-form";

export default function Page() {
  return (
    <main className="shell">
      <section className="hero">
        <span className="eyebrow">PaperTrace local MVP</span>
        <h1>Trace paper code lineage without touching a GPU.</h1>
        <p>
          This first build is optimized for local macOS development. The backend runs on Python, the
          web app stays in strict TypeScript, and the default pipeline stays fixture backed unless
          live repo scanning is explicitly enabled for shallow-clone analysis.
        </p>
      </section>
      <AnalysisForm />
    </main>
  );
}
