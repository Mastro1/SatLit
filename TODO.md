# SatLit — TODO

---

## 🔴 High Priority

- [x] **Fix local download** — the local download feature must work end-to-end
  - [x] Allow the user to select the destination folder
  - [x] Allow the user to specify a custom filename

- [ ] **Shape simplification** — add the ability to simplify geometries to speed up processing
  - [ ] Must work for both imported shapes and GADM shapes

---

## 🟡 Medium Priority

- [ ] **Implement crop map** — add a crop map layer/tool

- [ ] **Bitmask bands** — add possibility to apply bitmask to bands

- [ ] **Modularize GEE calls into a `GeeService`** — move the ~38 inline `ee.` calls out of `interface/main_panel.py` and `interface/sidebar.py` into `src/application/services/GeeService.py` (same pattern as `GeometryService`)
  - [ ] UI talks only to services, never to `ee` directly (thinner UI, testable without GEE)
  - [ ] Prerequisite for the online demo (see below)

---

## 📣 Visibility & Docs

- [ ] **Documentation site** — mkdocs-material site, one page per feature + use-case tutorials (NDVI for a farm, rainfall for a district)
  - [ ] Add `CITATION.cff` + Zenodo DOI per release; consider a JOSS submission once docs/tests are solid

- [ ] **Demo GIF in README** — short animated walkthrough: draw ROI → pick dataset → extract → result preview

- [ ] **Online UI demo on Streamlit Community Cloud** — full UI tour, no GEE account, no API key, extracts nothing
  - [ ] Separate demo layer in the same repo (no branch): `demo/run_demo.py` entry point + `demo/fakes/` that swap implementations at the service seams; `src/` stays unaware of demo
  - [ ] Fake storage: History/Preset/Settings backed by `st.session_state`, pre-seeded with sample presets and history (Cloud filesystem is shared across visitors)
  - [ ] Fake `GeeService` + `TaskWatcher`: "Extract" shows a dry-run summary of what would be submitted; seeded tasks move RUNNING → COMPLETED
  - [ ] Hide desktop-only features (desktop shortcut, desktop notifications)
  - [ ] Persistent banner: "Demo mode — install locally for real extraction"
  - [ ] CI smoke test: run demo entry point with `AppTest` and `ee` blocked, so any new inline `ee.` call in the UI fails loudly
  - [ ] Optional: `demo` pointer branch fast-forwarded to each release tag by a GitHub Action, if the demo should track releases instead of every commit on `main`

---

## 🟢 Nice to Have / Future

- [ ] **Add pixel visibility of the AOI** — Add the possibility to visualize the overlay of the pixels inside the AOI and download pixel per pixel data. 

- [ ] **Create a notification system** — Add a system that notifies the user when the download is complete or if there is an error. -> Already started with [This Repository](https://github.com/Mastro1/GEE-notification-system)

