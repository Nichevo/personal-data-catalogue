"""Getting things out of the catalogue: full exports, notes to share, the Copilot pack."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pdcat.export import to_csv_zip, to_json
from pdcat.store import Catalogue

from .common import cached, lazy_download


def export_page(cat: Catalogue) -> None:
    st.title("Export")
    st.caption("Everything in the catalogue: tables, columns, links, notes, profiles, functions, packages and "
               "what you know about each item. Sensitive values are never included. The files are built when "
               "you click, so this page stays quick.")
    from pdcat.screenshots import index_rows
    n_shots = len(index_rows(cat))
    shots = st.checkbox(f"Include report screenshots ({n_shots})", disabled=not n_shots, key="export-shots",
                        help="Left out unless ticked, since they can show real numbers. CSV zip only.")
    c1, c2 = st.columns(2)
    with c1:
        lazy_download("⬇️ CSV files (zip)", lambda c: to_csv_zip(c, screenshots=shots), "catalogue-export.zip",
                      "application/zip", f"export-csv-{shots}")
    with c2:
        lazy_download("⬇️ JSON", to_json, "catalogue-export.json", "application/json", "export-json")
    st.subheader("OpenMetadata")
    st.caption("Your SQL Server tables and views with your descriptions, the metric glossary and company-wide keys "
               "as a glossary, and table-to-table lineage, with a script that loads them into OpenMetadata.")
    from pdcat.openmeta import to_zip
    lazy_download("⬇️ OpenMetadata export (zip)", to_zip, "openmetadata-export.zip", "application/zip", "export-om")


def copilot_page(cat: Catalogue) -> None:
    st.title("Copilot pack")
    st.caption("What the catalogue knows, written as documents Microsoft 365 Copilot can search: business rules, "
               "a glossary, example questions, every table and file with its columns, joins and notes, reports, "
               "the feature dictionary and the code. Put the folder on OneDrive or SharePoint and add it to a "
               "Copilot agent as knowledge; agent-instructions has a description, instructions and starter prompts "
               "to paste.")
    tab_pack, tab_tests, tab_trip = st.tabs(["Pack", "Test questions", "Round trip"])
    with tab_pack:
        c1, c2, c3 = st.columns(3)
        hide = c1.checkbox("Hide server names and file locations", help="For sharing outside the team. Queries "
                                                                       "Copilot writes still name databases, schemas "
                                                                       "and tables.")
        values = c2.checkbox("Include value ranges and common values", help="Never for sensitive columns.")
        fmt = c3.radio("Format", ["md", "docx"], horizontal=True,
                       format_func={"md": "Markdown", "docx": "Word"}.get)
        docs = st.checkbox("Include reference documents", help="The text of the design documents kept with "
                                                               "business projects (latest version of each).")
        st.caption("Also in the pack: naming rules, SAP links, the SQL query library and a test-questions file.")
        from pdcat import aipack

        def build(c: Catalogue) -> bytes:
            return aipack.to_zip(c, hide_locations=hide, include_values=values, fmt=fmt, include_documents=docs)

        lazy_download("⬇️ Copilot pack (zip)", build, "copilot-pack.zip", "application/zip",
                      f"copilot-{hide}-{values}-{fmt}-{docs}")
        # Building the pack takes seconds on a big catalogue, so the preview is only built when asked for.
        if st.toggle("Preview the files", key="copilot-preview"):
            files = aipack.build(cat, hide_locations=hide, include_values=values, include_documents=docs)
            pick = st.selectbox("File", list(files))
            st.markdown(files[pick][:20000])
    with tab_tests:
        st.caption("Questions built from your own catalogue, with the answer the pack holds. Ask your Copilot agent "
                   "each one and check it gets them right. They're in the pack as test-questions.")
        qs = _test_questions(cat)
        if not qs:
            st.info("Not enough in the catalogue yet: add metrics, descriptions, joins or knowledge notes.")
        for i, q in enumerate(qs, 1):
            st.markdown(f"{i}. **{q['question']}**  \n   {q['answer'][:400]}")
    with tab_trip:
        copilot_round_trip(cat)


def _json(pack: dict) -> str:
    import json
    return json.dumps(pack, indent=1, ensure_ascii=False, default=str)


@cached
def _test_questions(cat: Catalogue) -> list[dict]:
    from pdcat.aipack import test_questions
    return test_questions(cat)


@cached
def _gap_count(cat: Catalogue) -> int:
    from pdcat.copilot_roundtrip import gaps
    return len(gaps(cat))


def copilot_round_trip(cat: Catalogue) -> None:
    from pdcat import copilot_roundtrip as R
    from pdcat import suggestions
    st.caption("Let your Copilot agent draft the missing descriptions. 1. Download the gaps pack and give it to "
               "the agent (attach it, or add it to its knowledge). 2. Paste its CSV reply below. 3. Accept or "
               "reject each answer as a suggestion. Nothing is sent anywhere by the catalogue.")
    n = _gap_count(cat)
    lazy_download(f"⬇️ Gaps pack ({n} item(s))", R.gaps_zip, "gaps-pack.zip", "application/zip", "gaps-pack")
    with st.form("copilot-answers", clear_on_submit=True):
        text = st.text_area("Copilot's answer (CSV with id, column, answer)", height=160, key="copilot-answer-text")
        up = st.file_uploader("…or upload it as a file", type=["csv", "txt", "md"], key="copilot-answer-file")
        if st.form_submit_button("Read the answers", type="primary"):
            body = text or (up.getvalue().decode("utf-8-sig", errors="replace") if up else "")
            got = R.read_answers(cat, body)
            if got["added"]:
                st.success(f"{got['added']} suggestion(s) added below."
                           + (f" {got['unknown']} answer(s) named items not in the catalogue and were skipped."
                              if got["unknown"] else ""))
            else:
                st.warning("No answers found. The reply needs a CSV with the columns id, column, answer.")
    if suggestions.open_suggestions(cat, "copilot", limit=1):
        from .groups_page import suggestion_picker
        st.markdown("**Answers waiting**")
        suggestion_picker(cat, "copilot", prefix="cp")


def sharing_page(cat: Catalogue) -> None:
    from pdcat import sharing as S
    st.title("Share with colleagues")
    st.caption("Swap what you know with a colleague who uses the catalogue too. Your file holds your notes, "
               "descriptions, knowledge, questions, metrics, features, keys, business projects and confirmed "
               "joins; it can hold your scans as well. Merging shows what's new first, keeps your own words where "
               "you both wrote something, and can be undone.")
    tab_send, tab_merge, tab_history = st.tabs(["Send mine", "Merge theirs", "Merged files"])
    with tab_send:
        sources = [s["name"] for s in S.source_list(cat)]
        scans = st.multiselect("Also include my scans of", sources, help="For colleagues who can't scan these "
                                                                         "themselves. Sensitive values are left out.")
        from pdcat.screenshots import index_rows
        n_shots = len(index_rows(cat))
        shots = st.checkbox(f"Include report screenshots ({n_shots})", disabled=not n_shots, key="share-shots",
                            help="Left out unless ticked, since they can show real numbers.")
        lazy_download("⬇️ My share file", lambda c: S.to_share_json(c, scans, screenshots=shots),
                      f"catalogue-share-{cat.author()}.json", "application/json",
                      f"share-{'-'.join(scans)}-{shots}")
        st.divider()
        st.markdown("**Questions for colleagues**")
        st.caption("A file holding only your open questions (question library entries with no answer yet). A "
                   "colleague merges it, answers the questions in their question library, and sends you their "
                   "answers file, which you merge here to fill in the answers.")
        n_open = sum(S._open_question(q) for q in S.questions_share(cat)["questions"])
        c1, c2 = st.columns(2)
        with c1:
            if n_open:
                lazy_download(f"⬇️ My {n_open} open question(s)", lambda c: _json(S.questions_share(c)),
                              f"questions-from-{cat.author()}.json", "application/json", "share-questions")
            else:
                st.caption("No open questions. Add one in Knowledge › Question library.")
        asked = S.asked_of_me(cat)
        with c2:
            answered = [q for q in asked if not S._open_question(q)]
            if answered:
                lazy_download(f"⬇️ My answers ({len(answered)})", lambda c: _json(S.answers_share(c)),
                              f"answers-from-{cat.author()}.json", "application/json", "share-answers")
            elif asked:
                st.caption(f"{len(asked)} question(s) from colleagues waiting in your question library.")
    with tab_merge:
        up = st.file_uploader("A colleague's share file", type=["json"])
        if up is not None:
            try:
                pack = S.read_share(up.getvalue())
            except ValueError as e:
                st.error(str(e))
                return
            merge_section(cat, pack, up.name)
    with tab_history:
        rows = S.batches(cat)
        if not rows:
            st.caption("Nothing merged yet.")
        for b in rows:
            with st.container(border=True):
                c1, c2 = st.columns([5, 1])
                total = sum(b["counts"].values()) + sum(s.get("added", 0) for s in b["scans"].values())
                c1.markdown(f"**{b['file'] or 'share file'}** from {b['author'] or 'someone'} · "
                            f"{b['imported_at'][:16].replace('T', ' ')} UTC · {total} changes"
                            + (" · undone" if b["undone"] else ""))
                c1.caption(", ".join(f"{n} {t.replace('_', ' ')}" for t, n in b["counts"].items() if n))
                if not b["undone"] and c2.button("Undo", key=f"undo-{b['id']}"):
                    S.undo(cat, b["id"])
                    st.toast("Undone")
                    st.rerun()


def merge_section(cat: Catalogue, pack: dict, file_name: str) -> None:
    from pdcat import sharing as S
    author = pack.get("author") or "someone"
    st.markdown(f"From **{author}**, made {pack.get('made_at', '')[:10]}.")
    mapping = S.suggest_mapping(cat, pack)
    mine = [s["name"] for s in S.source_list(cat)]
    if pack.get("sources"):
        st.markdown("**Their sources are your…**")
        for s in pack["sources"]:
            opts = list(dict.fromkeys([mapping[s["name"]], *mine, s["name"]]))
            label = s["name"] + (f" ({s.get('server', '')})" if s.get("server") else "")
            mapping[s["name"]] = st.selectbox(label, opts, key=f"map-{author}-{s['name']}",
                                              format_func=lambda o, n=s["name"]: o if o in mine else
                                              f"{o} (keep their name)")
    p = S.preview(cat, pack, mapping)
    rows = [{"what": t.replace("_", " "), **c} for t, c in p["sections"].items() if any(c.values())]
    if rows:
        st.dataframe(pd.DataFrame(rows).rename(columns={"fills": "fills your gaps"}), hide_index=True,
                     width="stretch")
    for source, s in p["scans"].items():
        st.caption(f"Their scan of {source}: {s['new']} of {s['items']} items are new to you"
                   + (" (you've scanned it too, so yours are kept)" if s["you_scanned"] else "") + ".")
    take: set = set()
    if p["conflicts"]:
        st.markdown(f"**You both wrote something different ({len(p['conflicts'])})**. Yours is kept unless you tick it.")
        for i, c in enumerate(p["conflicts"][:200]):
            for f in c["fields"]:
                col1, col2, col3 = st.columns([1, 3, 3])
                if col1.checkbox("Theirs", key=f"take-{i}-{f['field']}"):
                    take.add((c["table"], *c["pk"]))
                col2.caption(f"{c['table'].replace('_', ' ')} · {c['pk'][0]} · {f['field']}  \nMine: {f['mine']}")
                col3.caption(f"Theirs: {f['theirs']}")
    if st.button("Merge", type="primary"):
        result = S.merge(cat, pack, mapping, take_theirs=take, file_name=file_name)
        st.success(f"Merged. {sum(result['counts'].values())} changes"
                   + (f", {sum(s['added'] for s in result['scans'].values())} scanned items added"
                      if result["scans"] else "") + ". You can undo it under Merged files.")
