import { PageHeader } from "../components/ui";
import roadmapCards from "../roadmap.generated.json";

interface RoadmapCard { id: string; version: string; title: string; state: string; copy: string }

/** Generated from docs/features (features with a `roadmap:` block) by tools/check_docs.py --write. */
const roadmap: RoadmapCard[] = roadmapCards;

export function Roadmap() {
  return (
    <main className="roadmap-page">
      <PageHeader code="MAP" title="From data confidence to research execution."
        info="Each release builds on the same APIs, identifiers, and point-in-time ClickHouse data. Research, monitoring, live market views, and backtests share stable identifiers instead of creating parallel systems.">
        <a className="button-link" href="/">Open cockpit</a>
      </PageHeader>
      <ol className="roadmap" aria-label="FactorLab roadmap">
        {roadmap.map((item, index) => (
          <li className={index === 0 ? "roadmap-card roadmap-card--active" : "roadmap-card"} key={item.version}>
            <span className="roadmap-card__step" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
            <code>{item.version}</code>
            <h2>{item.title}</h2>
            <span className="roadmap-card__state">{item.state}</span>
            <p>{item.copy}</p>
          </li>
        ))}
      </ol>
    </main>
  );
}
