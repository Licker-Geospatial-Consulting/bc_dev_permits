/*
 * Dev Permit QA viewer — browser dashboard for eyeballing harvester output.
 *
 * Dynamic input: instead of a hard-coded data blob, this loads a JSON file at
 * runtime — either the default sample (data/processed/north_van_test.json, fetched
 * on start) or any file you pick with the "Load JSON" button. Point it at any
 * data/processed/<municipality>.json the harvester writes.
 *
 * How to run (needs a local static server so the browser can fetch the .jsx and the
 * JSON — file:// is blocked by the browser):
 *     make qa           # serves the repo root at http://localhost:8000
 * then open http://localhost:8000/tools/qa/
 *
 * No build step and no npm: index.html pulls React + Babel from a CDN and compiles
 * this file in the browser. React/ReactDOM are globals (not ES imports) for that reason.
 */

const { useState, useMemo, useEffect } = React;

// ---------------------------------------------------------------------------
// Adapter: map a harvester row (schema.sql field names) to the short keys this
// dashboard renders. Kept tolerant with ?? fallbacks so a file that already uses
// the short keys (e.g. an older export) still renders.
// ---------------------------------------------------------------------------
function normalize(rec) {
  const ms = rec.milestones ?? rec.ms ?? [];
  const docs = rec.documents ?? rec.docs ?? [];
  return {
    address: rec.address ?? null,
    permit_id: rec.permit_id ?? null,
    permit_type: rec.permit_type ?? null,
    dev_class: rec.development_class ?? rec.dev_class ?? null,
    status: rec.status ?? null,
    stories: rec.number_of_stories ?? rec.stories ?? null,
    units: rec.units_total ?? rec.units ?? null,
    unit_mix: rec.unit_mix ?? null,
    rental: rec.rental_or_strata ?? rec.rental ?? null,
    rental_sub: rec.rental_subtype ?? rec.rental_sub ?? null,
    rental_mix: rec.rental_mix ?? null,
    parking_v: rec.parking_vehicle_stalls ?? rec.parking_v ?? null,
    parking_b: rec.parking_bike_stalls ?? rec.parking_b ?? null,
    parking_notes: rec.parking_notes ?? null,
    floor_area: rec.floor_area ?? null,
    zoning: rec.zoning_density ?? rec.zoning ?? null,
    dev_name: rec.development_name ?? rec.dev_name ?? null,
    parsed: rec.is_parsed ?? rec.parsed ?? false,
    pdf_fb: rec.needs_pdf_extraction ?? rec.pdf_fb ?? false,
    review: rec.needs_review ?? rec.review ?? false,
    conf: rec.extraction_confidence ?? rec.conf ?? 0,
    method: rec.extraction_method ?? rec.method ?? null,
    url: rec.source_url ?? rec.url ?? null,
    prose: rec.raw_text ?? rec.prose ?? null,
    occ: rec.occupancy_types ?? rec.occ ?? [],
    ms: ms.map((m) => ({ m: m.milestone ?? m.m ?? "", d: m.milestone_date ?? m.d ?? null })),
    docs: docs.map((d) => ({ t: d.title ?? d.t ?? null, r: d.doc_role ?? d.r ?? null, u: d.url ?? d.u ?? null })),
    // Per-field provenance ({field: method}) and any HTML-vs-PDF disagreements.
    methods: rec.field_methods ?? {},
    conflicts: rec.conflicts ?? [],
  };
}

// How each extraction method is shown and whether it is trusted (deterministic) or a model
// guess (non-deterministic). Deterministic reads green, model reads amber/orange.
const METHOD_STYLE = {
  html:             { label: "html",       bg: "#dcfce7", fg: "#166534", det: true },
  pdf_text_regex:   { label: "pdf·regex",  bg: "#dcfce7", fg: "#166534", det: true },
  pdf_geometry:     { label: "pdf·geom",   bg: "#dcfce7", fg: "#166534", det: true },
  pdf_model_text:   { label: "pdf·model",  bg: "#fef3c7", fg: "#92400e", det: false },
  pdf_model_vision: { label: "pdf·vision", bg: "#fed7aa", fg: "#9a3412", det: false },
};

const isNonDeterministic = (r) =>
  Object.values(r.methods || {}).some((m) => METHOD_STYLE[m] && !METHOD_STYLE[m].det);

const CONF_COLORS = {
  0: "#ef4444", 0.2: "#f97316", 0.4: "#eab308",
  0.6: "#84cc16", 0.8: "#22c55e", 1: "#16a34a"
};

const confColor = (c) => CONF_COLORS[c] || "#6b7280";

const Badge = ({ children, bg = "#e5e7eb", fg = "#374151" }) => (
  <span style={{ background: bg, color: fg, padding: "2px 8px", borderRadius: 9999,
    fontSize: 11, fontWeight: 600, whiteSpace: "nowrap", display: "inline-block", margin: "1px 2px" }}>
    {children}
  </span>
);

const MethodTag = ({ method }) => {
  const s = METHOD_STYLE[method];
  if (!s) return null;
  return (
    <span title={method} style={{ background: s.bg, color: s.fg, padding: "0 6px", borderRadius: 9999,
      fontSize: 10, fontWeight: 700, whiteSpace: "nowrap", flexShrink: 0 }}>
      {s.label}
    </span>
  );
};

const Field = ({ label, value, method }) => {
  if (value == null || value === "" || (Array.isArray(value) && !value.length)) return null;
  const display = typeof value === "object" && !Array.isArray(value)
    ? Object.entries(value).map(([k, v]) => `${k}: ${v}`).join(", ")
    : Array.isArray(value) ? value.join(", ") : String(value);
  return (
    <div style={{ display: "flex", gap: 6, padding: "3px 0", borderBottom: "1px solid #f3f4f6", fontSize: 13, alignItems: "center" }}>
      <span style={{ color: "#6b7280", minWidth: 120, flexShrink: 0, fontWeight: 500 }}>{label}</span>
      <span style={{ color: "#111827", flex: 1 }}>{display}</span>
      <MethodTag method={method} />
    </div>
  );
};

const Row = ({ r, idx, expanded, toggle }) => {
  const conf = r.conf ?? 0;
  return (
    <div style={{ borderBottom: "1px solid #e5e7eb" }}>
      <div onClick={toggle} style={{ display: "grid", gridTemplateColumns: "minmax(180px,2fr) 1fr 90px 50px 50px 60px 42px",
        alignItems: "center", padding: "8px 12px", cursor: "pointer", gap: 8,
        background: expanded ? "#f8fafc" : idx % 2 === 0 ? "#fff" : "#fafafa",
        transition: "background .15s" }}
        onMouseEnter={e => e.currentTarget.style.background = "#f0f9ff"}
        onMouseLeave={e => e.currentTarget.style.background = expanded ? "#f8fafc" : idx % 2 === 0 ? "#fff" : "#fafafa"}>
        <div style={{ fontWeight: 500, fontSize: 13, color: "#111827", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {r.address}
        </div>
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
          {r.permit_type && <Badge bg="#dbeafe" fg="#1e40af">{r.permit_type}</Badge>}
          {r.dev_class && r.dev_class !== "unknown" && <Badge bg={
            r.dev_class === "residential" ? "#dcfce7" :
            r.dev_class === "commercial" ? "#fef3c7" :
            r.dev_class === "mixed" ? "#ede9fe" :
            r.dev_class === "industrial" ? "#ffedd5" : "#e0e7ff"
          } fg="#374151">{r.dev_class}</Badge>}
        </div>
        <div style={{ fontSize: 12, color: "#6b7280", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.status || "—"}</div>
        <div style={{ fontSize: 12, textAlign: "center" }}>{r.stories ?? "—"}</div>
        <div style={{ fontSize: 12, textAlign: "center" }}>{r.units ?? "—"}</div>
        <div style={{ textAlign: "center" }}>
          <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 4, background: confColor(conf), marginRight: 4 }} />
          <span style={{ fontSize: 11, color: "#374151" }}>{conf}</span>
        </div>
        <div style={{ fontSize: 14, textAlign: "center", color: "#9ca3af" }}>{expanded ? "▲" : "▼"}</div>
      </div>

      {expanded && (
        <div style={{ padding: "12px 16px 16px", background: "#f8fafc", display: "grid",
          gridTemplateColumns: "1fr 1fr", gap: "0 32px" }}>
          {r.conflicts.length > 0 && (
            <div style={{ gridColumn: "1/-1", marginBottom: 10, background: "#fff7ed",
              border: "1px solid #fdba74", borderRadius: 6, padding: "8px 12px" }}>
              <div style={{ fontWeight: 700, fontSize: 12, color: "#9a3412", marginBottom: 4 }}>
                ⚠ Text vs PDF conflicts ({r.conflicts.length})
              </div>
              {r.conflicts.map((c, i) => (
                <div key={i} style={{ fontSize: 12, color: "#7c2d12", padding: "1px 0" }}>
                  <b>{c.field}</b>: kept <b>{String(c.kept?.value)}</b>
                  <MethodTag method={c.kept?.method} /> &nbsp;vs PDF <b>{String(c.pdf?.value)}</b>
                  <MethodTag method={c.pdf?.method} />
                </div>
              ))}
            </div>
          )}
          <div>
            <div style={{ fontWeight: 600, fontSize: 12, color: "#6b7280", marginBottom: 4, textTransform: "uppercase", letterSpacing: ".5px" }}>Parsed Fields</div>
            <Field label="Permit ID" value={r.permit_id} />
            <Field label="Dev Name" value={r.dev_name} />
            <Field label="Type" value={r.permit_type} method={r.methods.permit_type} />
            <Field label="Class" value={r.dev_class} method={r.methods.development_class} />
            <Field label="Occupancy" value={r.occ} method={r.methods.occupancy_types} />
            <Field label="Stories" value={r.stories} method={r.methods.number_of_stories} />
            <Field label="Units" value={r.units} method={r.methods.units_total} />
            <Field label="Unit Mix" value={r.unit_mix} method={r.methods.unit_mix} />
            <Field label="Rental" value={r.rental} method={r.methods.rental_or_strata} />
            <Field label="Rental Sub" value={r.rental_sub} method={r.methods.rental_subtype} />
            <Field label="Rental Mix" value={r.rental_mix} />
            <Field label="Parking (V)" value={r.parking_v} method={r.methods.parking_vehicle_stalls} />
            <Field label="Parking (B)" value={r.parking_b} method={r.methods.parking_bike_stalls} />
            <Field label="Parking Notes" value={r.parking_notes} method={r.methods.parking_notes} />
            <Field label="Floor Area" value={r.floor_area} method={r.methods.floor_area} />
            <Field label="Zoning" value={r.zoning} method={r.methods.zoning_density} />
          </div>
          <div>
            <div style={{ fontWeight: 600, fontSize: 12, color: "#6b7280", marginBottom: 4, textTransform: "uppercase", letterSpacing: ".5px" }}>Extraction</div>
            <Field label="Parsed" value={r.parsed ? "✓" : "✗"} />
            <Field label="PDF Fallback" value={r.pdf_fb ? "Yes — needs Ollama" : "No"} />
            <Field label="Needs Review" value={r.review ? "⚠ Yes" : "No"} />
            <Field label="Confidence" value={conf} />
            <Field label="Method" value={r.method} />

            {r.prose && <>
              <div style={{ fontWeight: 600, fontSize: 12, color: "#6b7280", margin: "12px 0 4px", textTransform: "uppercase", letterSpacing: ".5px" }}>Source Prose</div>
              <div style={{ fontSize: 12, color: "#374151", background: "#fff", padding: 8, borderRadius: 6, border: "1px solid #e5e7eb", maxHeight: 120, overflow: "auto", lineHeight: 1.5 }}>
                {r.prose}
              </div>
            </>}

            {r.ms.length > 0 && <>
              <div style={{ fontWeight: 600, fontSize: 12, color: "#6b7280", margin: "12px 0 4px", textTransform: "uppercase", letterSpacing: ".5px" }}>Milestones</div>
              {r.ms.map((m, i) => (
                <div key={i} style={{ fontSize: 12, display: "flex", gap: 8, padding: "2px 0" }}>
                  <span style={{ color: "#6b7280", minWidth: 80 }}>{m.d || "pending"}</span>
                  <span>{m.m}</span>
                </div>
              ))}
            </>}

            {r.docs.length > 0 && <>
              <div style={{ fontWeight: 600, fontSize: 12, color: "#6b7280", margin: "12px 0 4px", textTransform: "uppercase", letterSpacing: ".5px" }}>Documents ({r.docs.length})</div>
              {r.docs.map((d, i) => (
                <div key={i} style={{ fontSize: 12, padding: "2px 0" }}>
                  <a href={d.u} target="_blank" rel="noopener" style={{ color: "#2563eb", textDecoration: "none" }}>
                    {d.t || "Untitled"}</a>
                  <Badge>{d.r}</Badge>
                </div>
              ))}
            </>}
          </div>
          <div style={{ gridColumn: "1/-1", marginTop: 8 }}>
            <a href={r.url} target="_blank" rel="noopener" style={{ fontSize: 12, color: "#2563eb" }}>
              Open source page →
            </a>
          </div>
        </div>
      )}
    </div>
  );
};

function QADashboard({ data }) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [classFilter, setClassFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [methodFilter, setMethodFilter] = useState("all");
  const [sortKey, setSortKey] = useState("conf_asc");
  const [expanded, setExpanded] = useState(new Set());

  const filtered = useMemo(() => {
    let rows = data.map((r, i) => ({ ...r, _i: i }));
    if (search) {
      const q = search.toLowerCase();
      rows = rows.filter(r => (r.address || "").toLowerCase().includes(q) ||
        (r.permit_id || "").toLowerCase().includes(q) ||
        (r.dev_name || "").toLowerCase().includes(q));
    }
    if (filter === "pdf_fallback") rows = rows.filter(r => r.pdf_fb);
    else if (filter === "needs_review") rows = rows.filter(r => r.review && !r.pdf_fb);
    else if (filter === "ok") rows = rows.filter(r => !r.review && !r.pdf_fb);
    else if (filter === "low_conf") rows = rows.filter(r => (r.conf ?? 0) <= 0.4);
    else if (filter === "conflicts") rows = rows.filter(r => (r.conflicts || []).length);
    else if (filter === "nondeterministic") rows = rows.filter(isNonDeterministic);

    if (classFilter !== "all") rows = rows.filter(r => r.dev_class === classFilter);
    if (typeFilter !== "all") rows = rows.filter(r => (r.permit_type || "None") === typeFilter);
    if (methodFilter !== "all") rows = rows.filter(r => (r.method || "None") === methodFilter);

    if (sortKey === "conf_asc") rows.sort((a, b) => (a.conf ?? 0) - (b.conf ?? 0));
    else if (sortKey === "conf_desc") rows.sort((a, b) => (b.conf ?? 0) - (a.conf ?? 0));
    else if (sortKey === "address") rows.sort((a, b) => (a.address || "").localeCompare(b.address || ""));
    else if (sortKey === "units_desc") rows.sort((a, b) => (b.units ?? 0) - (a.units ?? 0));
    return rows;
  }, [data, search, filter, classFilter, typeFilter, methodFilter, sortKey]);

  // Distinct extraction methods present in the data (e.g. html, html+ollama_pdf, arcgis).
  const methods = useMemo(
    () => Array.from(new Set(data.map(r => r.method || "None"))).sort(),
    [data]
  );

  const stats = useMemo(() => ({
    total: data.length,
    parsed: data.filter(r => r.parsed).length,
    pdfFb: data.filter(r => r.pdf_fb).length,
    review: data.filter(r => r.review).length,
    conflicts: data.filter(r => (r.conflicts || []).length).length,
  }), [data]);

  const toggle = (i) => setExpanded(prev => {
    const next = new Set(prev);
    next.has(i) ? next.delete(i) : next.add(i);
    return next;
  });

  const selStyle = { padding: "6px 10px", borderRadius: 6, border: "1px solid #d1d5db", fontSize: 12, background: "#fff", color: "#374151" };

  return (
    <div>
      {/* header stats */}
      <div style={{ display: "flex", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
        {[
          { label: "Total", val: stats.total, bg: "#f3f4f6" },
          { label: "Parsed", val: stats.parsed, bg: "#dcfce7" },
          { label: "PDF Fallback", val: stats.pdfFb, bg: "#fee2e2" },
          { label: "Needs Review", val: stats.review, bg: "#fef3c7" },
          { label: "Conflicts", val: stats.conflicts, bg: "#ffedd5" },
        ].map(s => (
          <div key={s.label} style={{ background: s.bg, borderRadius: 8, padding: "8px 16px", minWidth: 90, textAlign: "center" }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: "#111827" }}>{s.val}</div>
            <div style={{ fontSize: 11, color: "#6b7280" }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* filters */}
      <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap", alignItems: "center" }}>
        <input value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Search address, permit ID, name…"
          style={{ ...selStyle, flex: "1 1 200px", minWidth: 160 }} />
        <select value={filter} onChange={e => setFilter(e.target.value)} style={selStyle}>
          <option value="all">All QA states</option>
          <option value="pdf_fallback">PDF fallback only</option>
          <option value="needs_review">Needs review</option>
          <option value="ok">OK (no issues)</option>
          <option value="conflicts">Has text/PDF conflict</option>
          <option value="nondeterministic">Has model-derived field</option>
        </select>
        <select value={classFilter} onChange={e => setClassFilter(e.target.value)} style={selStyle}>
          <option value="all">All classes</option>
          <option value="residential">Residential</option>
          <option value="commercial">Commercial</option>
          <option value="mixed">Mixed</option>
          <option value="industrial">Industrial</option>
          <option value="institutional">Institutional</option>
          <option value="unknown">Unknown</option>
        </select>
        <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)} style={selStyle}>
          <option value="all">All permit types</option>
          <option value="Rezoning">Rezoning</option>
          <option value="Development Permit">Dev Permit</option>
          <option value="Development Variance Permit">DVP</option>
          <option value="OCP Amendment">OCP Amendment</option>
          <option value="Temporary Use Permit">TUP</option>
          <option value="None">No type parsed</option>
        </select>
        <select value={methodFilter} onChange={e => setMethodFilter(e.target.value)} style={selStyle}>
          <option value="all">All methods</option>
          {methods.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <select value={sortKey} onChange={e => setSortKey(e.target.value)} style={selStyle}>
          <option value="conf_asc">Confidence ↑ (worst first)</option>
          <option value="conf_desc">Confidence ↓</option>
          <option value="address">Address A–Z</option>
          <option value="units_desc">Units ↓</option>
        </select>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
        <div style={{ fontSize: 12, color: "#6b7280" }}>{filtered.length} of {stats.total} rows shown</div>
        <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: "#6b7280" }}>
          <span>Field source:</span>
          <MethodTag method="html" /> <MethodTag method="pdf_text_regex" /> <MethodTag method="pdf_geometry" />
          <span style={{ color: "#9ca3af" }}>deterministic ·</span>
          <MethodTag method="pdf_model_text" /> <MethodTag method="pdf_model_vision" />
          <span style={{ color: "#9ca3af" }}>model (review)</span>
        </div>
      </div>

      {/* table header */}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(180px,2fr) 1fr 90px 50px 50px 60px 42px",
        padding: "6px 12px", background: "#f1f5f9", borderRadius: "8px 8px 0 0", gap: 8,
        fontSize: 11, fontWeight: 600, color: "#64748b", textTransform: "uppercase", letterSpacing: ".3px" }}>
        <div>Address</div><div>Type / Class</div><div>Status</div>
        <div style={{ textAlign: "center" }}>Stry</div>
        <div style={{ textAlign: "center" }}>Units</div>
        <div style={{ textAlign: "center" }}>Conf</div>
        <div />
      </div>

      {/* rows */}
      <div style={{ border: "1px solid #e5e7eb", borderTop: "none", borderRadius: "0 0 8px 8px", overflow: "hidden" }}>
        {filtered.map((r, i) => (
          <Row key={r._i} r={r} idx={i} expanded={expanded.has(r._i)} toggle={() => toggle(r._i)} />
        ))}
        {filtered.length === 0 && (
          <div style={{ padding: 32, textAlign: "center", color: "#9ca3af", fontSize: 14 }}>
            No rows match the current filters.
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// App shell: owns the dynamic JSON input (default fetch + file picker) and hands
// the normalized rows to the dashboard.
// ---------------------------------------------------------------------------
function App() {
  const DEFAULT = window.QA_DEFAULT_JSON || "/data/processed/north_van_test.json";
  const [rows, setRows] = useState(null);
  const [source, setSource] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = (parsed, name) => {
    if (!Array.isArray(parsed)) {
      setError(`${name}: expected a JSON array of permit rows, got ${typeof parsed}.`);
      return;
    }
    setRows(parsed.map(normalize));
    setSource(name);
    setError("");
  };

  useEffect(() => {
    let cancelled = false;
    fetch(DEFAULT)
      .then(res => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
      })
      .then(json => { if (!cancelled) load(json, DEFAULT.split("/").pop()); })
      .catch(err => {
        if (!cancelled) {
          setError(`Could not auto-load ${DEFAULT} (${err.message}). Pick a file with "Load JSON", or run "make qa" from the repo root and open http://localhost:8000/tools/qa/.`);
        }
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const onFile = (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try { load(JSON.parse(reader.result), file.name); }
      catch (err) { setError(`Failed to parse ${file.name}: ${err.message}`); }
    };
    reader.readAsText(file);
  };

  return (
    <div style={{ fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", maxWidth: 1100, margin: "0 auto", padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 16,
        paddingBottom: 12, borderBottom: "1px solid #e5e7eb" }}>
        <div style={{ fontSize: 18, fontWeight: 700, color: "#111827" }}>Dev Permit QA</div>
        <label style={{ fontSize: 12, fontWeight: 600, color: "#2563eb", cursor: "pointer",
          border: "1px solid #bfdbfe", borderRadius: 6, padding: "6px 12px", background: "#eff6ff" }}>
          Load JSON…
          <input type="file" accept=".json,application/json" onChange={onFile} style={{ display: "none" }} />
        </label>
        <span style={{ fontSize: 12, color: "#6b7280" }}>
          {source ? `Source: ${source}` : loading ? "Loading…" : "No data loaded"}
        </span>
      </div>

      {error && (
        <div style={{ background: "#fef2f2", border: "1px solid #fecaca", color: "#991b1b",
          borderRadius: 8, padding: "10px 14px", fontSize: 13, marginBottom: 16, lineHeight: 1.5 }}>
          {error}
        </div>
      )}

      {rows ? <QADashboard data={rows} />
        : loading ? <div style={{ padding: 32, textAlign: "center", color: "#9ca3af" }}>Loading…</div>
        : null}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
