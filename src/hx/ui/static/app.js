"use strict";
// All views consume the version 2 packet projection. Worker reports are claims;
// check receipts and lifecycle milestones remain separately visible.
const esc = value => String(value ?? "—").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const link = id => `<a href="#show/${encodeURIComponent(id)}">${esc(id)}</a>`;
const json = value => `<pre>${esc(JSON.stringify(value, null, 2))}</pre>`;
const empty = text => `<div class="empty">${esc(text)}</div>`;
const table = (headers, rows) => rows.length ? `<div class="table-wrap"><table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${v}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : empty("No recorded entries.");
const tag = text => `<span class="tag">${esc(text)}</span>`;
let board = null;
let refreshVersion = 0;
async function api(path, options) {
  const response = await fetch(path, {credentials:"same-origin", cache:"no-store", ...options});
  const value = await response.json();
  if (!response.ok) throw Error(value.error || `${response.status}: request failed`);
  return value;
}
function packets(view) {
  return view.tasks.length ? `<div class="cards">${view.tasks.map(t => {
    const p = t.payload;
    return `<article class="card"><div class="eyebrow">${link(p.plan_id)} · revision ${t.revision}</div><h3>${link(t.task_id)}</h3>${tag(t.status)}${t.worker_id ? tag(t.worker_id) : ""}<p>${esc(p.goal)}</p><div class="dependency">${p.prerequisites.length ? p.prerequisites.map(d=>`← ${link(d.task_id)} / ${esc(d.output_id)} @${d.version} ${tag(d.need)}`).join("<br>") : "Root packet · no prerequisites"}</div></article>`;
  }).join("")}</div>` : empty("No packets have been planned. The Partner turns each directive into a dependency graph.");
}
function runs(view) {
  return table(["Packet / run", "Worker", "Phase", "Native execution", "Outcome"], view.runs.map(r=>[`${link(r.task_id)}<br>${link(r.run_id)}`,esc(r.worker_id),esc(r.phase),`${esc(r.native_status)}${r.native_error ? `<p class="warning">${esc(r.native_error)}</p>` : ""}`,esc(r.outcome)]));
}
function delivery(view) {
  return `<h2>Changes</h2>${table(["Change", "Revision", "Head", "Status"],view.delivery.changes.map(c=>[esc(c.change_id),esc(c.revision),`<code>${esc(c.head)}</code>`,tag(c.status)]))}<h2>Fixed candidates</h2>${table(["Candidate", "Boundary", "Source head", "Status"],view.delivery.candidates.map(c=>[esc(c.batch_id),esc(c.boundary),`<code>${esc(c.head)}</code>`,tag(c.status)]))}<p>Recorded candidate status is tied to its exact source head. Revalidate proof before landing or releasing.</p>`;
}
function detail(view) {
  return `<div class="eyebrow">Assignment / ${esc(view.identity)}</div><h1>Packet detail</h1>${packets(view)}<h2>Execution</h2>${runs(view)}<h2>Worker progress</h2><p>Worker reports describe current intent and blockers; they do not establish passing checks.</p>${view.progress.length ? view.progress.map(p=>`<details open><summary>${esc(p.task_id)} · update ${p.revision}</summary>${json({cursor:p.payload,steps:p.steps,deliverables:p.deliverables})}</details>`).join("") : empty("No progress reported.")}<h2>Write ownership</h2>${table(["Repository", "Path", "Run"],view.leases.map(l=>[esc(l.repository),esc(l.canonical_path),link(l.run_id)]))}<h2>Check receipts</h2>${table(["Check", "Receipt", "Run", "Exit", "Valid"],view.checks.map(c=>[esc(c.check_id),esc(c.receipt_id),link(c.run_id),esc(c.exit_code),esc(Boolean(c.valid))]))}<h2>Checkpoints</h2>${table(["Checkpoint", "Run", "Task revision", "Acknowledged"],view.checkpoints.map(c=>[esc(c.checkpoint_id),link(c.run_id),esc(c.task_revision),esc(Boolean(c.acknowledged))]))}<h2>Milestones</h2>${table(["Packet", "Kind", "Identity"],view.milestones.map(m=>[link(m.task_id),tag(m.kind),esc(m.identity)]))}${delivery(view)}`;
}
function partner() {
  return `<div class="eyebrow">Human direction</div><h1>Partner</h1><p>Send a directive or correction to the Partner. Each task enters the same packet planning and admission flow.</p><form id="partner-form"><label for="partner-text">Message</label><textarea id="partner-text" rows="6" required maxlength="16000"></textarea><button type="submit">Send to Partner</button><p id="wake-status" role="status"></p></form>`;
}
async function render() {
  const version = ++refreshVersion;
  const route = location.hash.slice(1) || "overview";
  document.querySelectorAll("nav a").forEach(a=>a.setAttribute("aria-current",a.hash === `#${route}` ? "page" : "false"));
  if (route === "partner") {
    document.getElementById("content").innerHTML = partner();
    document.getElementById("partner-form").addEventListener("submit",async event=>{
      event.preventDefault();
      const button=event.target.querySelector("button"); button.disabled=true;
      try {
        const result=await api("/api/partner/wake",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text:document.getElementById("partner-text").value})});
        document.getElementById("wake-status").textContent=result.delivered ? "Delivered to Partner." : `Not delivered: ${result.status}`;
      } catch(error) { document.getElementById("wake-status").textContent=error.message; }
      finally { button.disabled=false; }
    });
    return;
  }
  try {
    const view=await api(route.startsWith("show/") ? `/api/show/${encodeURIComponent(decodeURIComponent(route.slice(5)))}` : "/api/board");
    if (version !== refreshVersion) return;
    if (view.schema_version !== 2) throw Error("Expected the packet projection.");
    board=view;
    const active=view.runs.filter(r=>r.ended_at === null).length;
    let html=route.startsWith("show/") ? detail(view) : route === "runs" ? `<h1>Execution and ownership</h1>${runs(view)}<h2>Leases</h2>${table(["Repository","Path","Run"],view.leases.map(l=>[esc(l.repository),esc(l.canonical_path),link(l.run_id)]))}` : route === "delivery" ? `<h1>Delivery evidence</h1>${delivery(view)}` : `<div class="eyebrow">Dependency graphs</div><h1>Planned work</h1><p>Every packet carries a result, exact dependencies, ownership and proof obligations.</p><div class="stats">${[[view.plans.length,"Plans"],[view.tasks.length,"Packets"],[active,"Active runs"],[view.leases.length,"Write leases"]].map(([n,label])=>`<div class="stat"><strong>${n}</strong><span>${label}</span></div>`).join("")}</div>${packets(view)}`;
    if(view.truncated) html+=`<p class="warning">Overview capped. Open a packet or plan for its selected records.</p>`;
    document.getElementById("content").innerHTML=html;
    document.getElementById("footer-info").textContent=`${view.root_abs} · revision ${view.ledger_revision} · ${view.ts}`;
    document.getElementById("error-banner").hidden=true;
  } catch(error) {
    if(version !== refreshVersion) return;
    const banner=document.getElementById("error-banner");banner.hidden=false;banner.textContent=error.message;
  }
}
document.getElementById("refresh").addEventListener("click",render);
window.addEventListener("hashchange",render);
const events=new EventSource("/api/events");
events.onopen=()=>{document.getElementById("connection").textContent="Live";};
events.onerror=()=>{document.getElementById("connection").textContent="Reconnecting";};
events.onmessage=()=>{if(location.hash !== "#partner")render();};
render();
