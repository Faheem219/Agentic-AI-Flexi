import { DragEvent, FormEvent, useEffect, useRef, useState } from "react";
import { api, KBDocument, KBHit } from "../api";

export default function LibraryPage() {
  const [docs, setDocs] = useState<KBDocument[]>([]);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<KBHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = () => api.kbList().then(setDocs).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      for (const f of Array.from(files)) {
        const doc = await api.kbUpload(f);
        setNotice(`Added "${doc.title}": ${doc.chunks} passages from ${doc.pages} pages${doc.embedded ? "" : " (keyword search only; embeddings unavailable)"}.`);
      }
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function search(e: FormEvent) {
    e.preventDefault();
    if (query.trim().length < 2) return;
    setSearching(true);
    try { setHits(await api.kbSearch(query.trim(), 5)); } catch (err) { setError((err as Error).message); }
    setSearching(false);
  }

  const onDrop = (e: DragEvent) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files); };

  return (
    <div>
      <header className="page-head">
        <h1>Your library</h1>
        <p>Add your own papers, notes or reports. When "Your library" is selected as a source, researchers search these documents alongside the public databases and cite them by page.</p>
      </header>
      <div className="two-col">
        <section>
          <div className={`dropzone ${drag ? "drag" : ""}`} role="button" tabIndex={0}
               onClick={() => input.current?.click()} onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
               onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDrop}>
            {busy ? <><span className="spinner" /> Reading, chunking and embedding the document</> : (
              <><b>Drop PDF, TXT or Markdown files here</b><br /><span className="small muted">or click to choose files (up to 15 MB each)</span></>
            )}
            <input ref={input} type="file" accept=".pdf,.txt,.md,.markdown" multiple hidden onChange={(e) => upload(e.target.files)} />
          </div>
          {notice && <p className="notice ok" style={{ marginTop: 12 }}>{notice}</p>}
          {error && <p className="notice" style={{ marginTop: 12 }} role="alert">{error}</p>}
          <div className="table-wrap" style={{ marginTop: 18 }}>
            {docs.length === 0 ? <p className="empty">No documents yet. Upload one to let the agents cite it.</p> : (
              <table className="data">
                <thead><tr><th>Document</th><th className="num">Pages</th><th className="num">Passages</th><th>Search</th><th /></tr></thead>
                <tbody>
                  {docs.map((d) => (
                    <tr key={d.id}>
                      <td><b>{d.title}</b><br /><span className="tiny muted">{d.filename}</span></td>
                      <td className="num">{d.pages}</td>
                      <td className="num">{d.chunks}</td>
                      <td>{d.embedded ? "Hybrid" : "Keyword only"}</td>
                      <td><button className="btn small quiet" onClick={async () => { await api.kbDelete(d.id); load(); }}>Remove</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
        <section className="panel">
          <div className="panel-head"><h2>Test retrieval</h2><span className="tiny muted">BM25 + embeddings, fused by reciprocal rank</span></div>
          <div style={{ padding: 16 }}>
            <form className="row" onSubmit={search}>
              <label htmlFor="kbq" className="visually-hidden">Search your library</label>
              <input id="kbq" className="input" style={{ flex: 1 }} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Ask something your documents should answer" />
              <button className="btn" disabled={searching} type="submit">Search</button>
            </form>
            {hits && hits.length === 0 && <p className="empty">No passages matched. Try different words.</p>}
            {hits?.map((h) => (
              <div key={h.chunk_id} className="hit">
                <b>{h.doc_title}</b>, page {h.page}
                <p style={{ marginTop: 4 }}>{h.text.replace(/^\[[^\]]*\]\s*/, "").slice(0, 420)}...</p>
                <div className="ranks">
                  <span>Fused score {h.rrf_score.toFixed(4)}</span>
                  <span>Keyword rank {h.bm25_rank ?? "none"}</span>
                  <span>Semantic rank {h.dense_rank ?? "none"}</span>
                  {h.cosine != null && <span>Cosine {h.cosine.toFixed(3)}</span>}
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
