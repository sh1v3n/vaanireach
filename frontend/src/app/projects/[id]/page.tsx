export default function ProjectReviewPage({ params }: { params: { id: string } }) {
  return (
    <main style={{ padding: "3rem", maxWidth: 720 }}>
      <h1>Project {params.id}</h1>
      <p style={{ marginTop: "1rem" }}>
        This route is reserved for the future Review Dashboard — Source
        pane, Generated Content pane, Verification pane, and
        Approve / Reject / Regenerate / Edit actions.
      </p>
      <p style={{ marginTop: "1rem", color: "#888" }}>Coming in Phase 3.</p>
    </main>
  );
}
