import { AnalysisForm } from "@/components/analysis-form";

export default function Page() {
  return (
    <main className="shell">
      <section className="hero">
        <span className="eyebrow">PaperTrace local MVP</span>
        <h1>Trace paper code lineage without touching a GPU.</h1>
        <p>
          Paste an arXiv link, a PDF URL, or upload a PDF. The app resolves the code repository from the paper and turns
          the result into a reviewable change map.
        </p>
      </section>
      <AnalysisForm />
    </main>
  );
}
