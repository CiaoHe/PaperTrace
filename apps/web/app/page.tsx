import { AnalysisForm } from "@/components/analysis-form";

export default function Page() {
  return (
    <main className="shell">
      <section className="hero">
        <span className="eyebrow">PaperTrace local MVP</span>
        <h1>Trace paper code lineage without touching a GPU.</h1>
        <p>
          This first build is optimized for local macOS development. The backend runs on Python, the
          web app stays in strict TypeScript, and the default pipeline can progressively enable real
          arXiv fetch, repo tracing, and shallow-clone diff analysis while keeping fixture fallback
          paths available for deterministic local testing.
        </p>
      </section>
      <AnalysisForm />
    </main>
  );
}
