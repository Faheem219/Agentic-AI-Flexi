import { Source } from "../api";

const KIND: Record<string, string> = {
  openalex: "OpenAlex",
  arxiv: "arXiv preprint",
  wikipedia: "Wikipedia",
  kb: "Your library",
  crossref: "Crossref",
};

function authorLine(authors: string[]) {
  if (!authors?.length) return null;
  return authors.length > 3 ? `${authors.slice(0, 3).join(", ")} et al.` : authors.join(", ");
}

/** A source rendered as a library catalogue card. `call` is the citation number or source id. */
export default function CatalogCard({ source, call, sub, className = "" }: { source: Source; call: string; sub?: string; className?: string }) {
  const details = [source.venue, source.year, source.citations != null ? `cited by ${source.citations.toLocaleString()}` : null]
    .filter(Boolean)
    .join(", ");
  return (
    <article className={`catalog-card ${className}`}>
      <div className="cc-top">
        <span className="cc-call">{call}{sub && <span className="cc-sub">{sub}</span>}</span>
        <span className="cc-kind">{KIND[source.type] ?? source.type}</span>
      </div>
      <div className="cc-body">
        <div className="cc-title">{source.title}</div>
        {authorLine(source.authors) && <div className="cc-line">{authorLine(source.authors)}</div>}
        {details && <div className="cc-line">{details}</div>}
        {source.abstract && <p className="cc-abstract">{source.abstract}</p>}
        <div className="cc-actions">
          {source.url && <a href={source.url} target="_blank" rel="noreferrer">Open source</a>}
          {source.doi && <span className="muted cc-doi">DOI {source.doi}</span>}
        </div>
      </div>
    </article>
  );
}
