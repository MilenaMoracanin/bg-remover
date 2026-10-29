const PRESETS = {
  kp: {
    name: "KupujemProdajem",
    detail: "Bela pozadina, kvadrat 1200",
    background: "#ffffff",
    width: 1200,
    height: 1200,
    shadow: true,
    format: "jpeg",
    anchor: 0.5,
    ratio: "1 / 1",
  },
  ig_feed: {
    name: "Instagram objava",
    detail: "4:5, svetla studio pozadina",
    background: "#f3f1ec",
    width: 1080,
    height: 1350,
    shadow: true,
    format: "jpeg",
    anchor: 0.48,
    ratio: "4 / 5",
  },
  ig_square: {
    name: "Instagram kvadrat",
    detail: "1080×1080, bela pozadina",
    background: "#ffffff",
    width: 1080,
    height: 1080,
    shadow: true,
    format: "jpeg",
    anchor: 0.5,
    ratio: "1 / 1",
  },
  ig_story: {
    name: "Instagram priča",
    detail: "9:16, proizvod iznad dugmadi",
    background: "#f6f4ef",
    width: 1080,
    height: 1920,
    shadow: true,
    format: "jpeg",
    anchor: 0.36,
    ratio: "9 / 16",
  },
  transparent: {
    name: "Providna PNG",
    detail: "Bez pozadine, za Canva",
    background: "transparent",
    width: 0,
    height: 0,
    shadow: false,
    format: "png",
    anchor: 0.5,
    ratio: "1 / 1",
  },
};

const SWATCHES = ["#ffffff", "#f3f1ec", "#e7e2d6", "#d9d9d9", "#1c1915"];

const files = [];
let presetId = "kp";
let background = PRESETS.kp.background;
let jobId = "";

const drop = document.querySelector("#drop");
const fileInput = document.querySelector("#file");
const queue = document.querySelector("#queue");
const presets = document.querySelector("#presets");
const swatches = document.querySelector("#swatches");
const colorInput = document.querySelector("#color");
const colorField = document.querySelector("#color-field");
const shadowInput = document.querySelector("#shadow");
const spec = document.querySelector("#spec");
const runButton = document.querySelector("#run");
const progress = document.querySelector("#progress");
const statusEl = document.querySelector("#status");
const results = document.querySelector("#results");
const grid = document.querySelector("#grid");
const zipButton = document.querySelector("#zip");

function selectedPreset() {
  return PRESETS[presetId];
}

function renderPresets() {
  presets.innerHTML = "";
  for (const [id, preset] of Object.entries(PRESETS)) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "preset" + (id === presetId ? " selected" : "");
    const ratio = document.createElement("span");
    ratio.className = "ratio";
    ratio.style.width = id === "ig_story" ? "18px" : id === "ig_feed" ? "24px" : "32px";
    const text = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = preset.name;
    const detail = document.createElement("span");
    detail.textContent = preset.detail;
    text.append(name, detail);
    button.append(ratio, text);
    button.addEventListener("click", () => applyPreset(id));
    presets.append(button);
  }
}

function renderSwatches() {
  swatches.innerHTML = "";
  for (const color of SWATCHES) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "swatch" + (background === color ? " selected" : "");
    button.style.background = color;
    button.setAttribute("aria-label", color);
    button.addEventListener("click", () => {
      background = color;
      colorInput.value = color;
      renderSwatches();
      updateSpec();
    });
    swatches.append(button);
  }
}

function applyPreset(id) {
  presetId = id;
  const preset = PRESETS[id];
  background = preset.background;
  shadowInput.checked = preset.shadow;
  if (preset.background !== "transparent") colorInput.value = preset.background;
  renderPresets();
  renderSwatches();
  updateSpec();
}

function updateSpec() {
  const preset = selectedPreset();
  const transparent = preset.format === "png" && preset.width === 0;
  colorField.disabled = transparent;
  shadowInput.disabled = transparent;
  if (transparent) {
    spec.textContent = "PNG bez pozadine, u veličini proizvoda. Pogodno za Canva.";
  } else {
    const shadow = shadowInput.checked ? "sa senkom" : "bez senke";
    spec.textContent = `JPEG najvišeg kvaliteta · ${preset.width}×${preset.height} · ${shadow} · beli razmak, ivica ostaje ako je već u kadru`;
  }
  runButton.disabled = files.length === 0 || runButton.dataset.busy === "1";
}

function addFiles(list) {
  for (const file of list) {
    if (!file.type.startsWith("image/") && !/\.(heic|heif)$/i.test(file.name)) continue;
    if (files.length >= 30) break;
    files.push(file);
  }
  renderQueue();
  updateSpec();
}

function renderQueue() {
  queue.innerHTML = "";
  files.forEach((file, index) => {
    const item = document.createElement("li");
    const img = document.createElement("img");
    img.src = URL.createObjectURL(file);
    img.alt = "";
    const text = document.createElement("div");
    const name = document.createElement("p");
    name.className = "name";
    name.textContent = file.name;
    const meta = document.createElement("p");
    meta.className = "meta";
    meta.textContent = `${Math.max(1, Math.round(file.size / 1024))} KB`;
    text.append(name, meta);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", "Ukloni");
    remove.addEventListener("click", () => {
      URL.revokeObjectURL(img.src);
      files.splice(index, 1);
      renderQueue();
      updateSpec();
    });
    item.append(img, text, remove);
    queue.append(item);
  });
}

drop.addEventListener("dragover", (event) => {
  event.preventDefault();
  drop.classList.add("over");
});
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (event) => {
  event.preventDefault();
  drop.classList.remove("over");
  addFiles(event.dataTransfer.files);
});
fileInput.addEventListener("change", () => {
  addFiles(fileInput.files);
  fileInput.value = "";
});

shadowInput.addEventListener("change", updateSpec);
colorInput.addEventListener("input", () => {
  background = colorInput.value.toLowerCase();
  renderSwatches();
  updateSpec();
});

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

function resultCard(file, blob, filename, width, height, seconds) {
  const card = document.createElement("article");
  card.className = "card";
  const compare = document.createElement("div");
  compare.className = "compare";
  compare.style.setProperty("--ratio", `${width} / ${height}`);

  const after = document.createElement("img");
  after.className = "after";
  after.src = URL.createObjectURL(blob);
  after.alt = "Slika bez pozadine";

  const before = document.createElement("img");
  before.className = "before";
  before.src = URL.createObjectURL(file);
  before.alt = "Original";

  const beforeTag = document.createElement("span");
  beforeTag.className = "tag before";
  beforeTag.textContent = "Pre";
  const afterTag = document.createElement("span");
  afterTag.className = "tag after";
  afterTag.textContent = "Posle";

  const slider = document.createElement("input");
  slider.type = "range";
  slider.min = "0";
  slider.max = "100";
  slider.value = "50";
  slider.addEventListener("input", () => {
    before.style.clipPath = `inset(0 ${100 - Number(slider.value)}% 0 0)`;
  });

  compare.append(after, before, beforeTag, afterTag, slider);

  const title = document.createElement("h3");
  title.textContent = filename;
  const meta = document.createElement("p");
  meta.className = "meta";
  meta.textContent = `${width}×${height} · ${seconds} s`;

  const actions = document.createElement("div");
  actions.className = "card-actions";
  const download = document.createElement("button");
  download.type = "button";
  download.textContent = "Preuzmi";
  download.addEventListener("click", () => saveBlob(blob, filename));
  actions.append(download);

  card.append(compare, title, meta, actions);
  return card;
}

function errorCard(file, message) {
  const card = document.createElement("article");
  card.className = "card error";
  const title = document.createElement("h3");
  title.textContent = file.name;
  const text = document.createElement("p");
  text.textContent = message;
  card.append(title, text);
  return card;
}

runButton.addEventListener("click", async () => {
  if (!files.length || runButton.dataset.busy === "1") return;
  runButton.dataset.busy = "1";
  runButton.disabled = true;
  jobId = crypto.randomUUID();
  results.hidden = false;
  grid.innerHTML = "";
  progress.hidden = false;

  const preset = selectedPreset();
  const batch = files.slice();
  for (let index = 0; index < batch.length; index += 1) {
    const file = batch[index];
    progress.textContent = `Obrađujem ${index + 1} od ${batch.length}: ${file.name}. Jedna slika traje oko 20 sekundi.`;
    const body = new FormData();
    body.append("file", file);
    body.append("job", jobId);
    body.append("background", preset.width === 0 ? "transparent" : background);
    body.append("width", String(preset.width));
    body.append("height", String(preset.height));
    body.append("shadow", shadowInput.checked ? "1" : "0");
    body.append("image_format", preset.format);
    body.append("anchor", String(preset.anchor));
    body.append("label", presetId);

    try {
      const response = await fetch("/api/process", { method: "POST", body });
      if (!response.ok) {
        let message = "Obrada nije uspela.";
        try {
          const payload = await response.json();
          message = payload.detail || message;
        } catch (_) {
          /* keep default */
        }
        grid.append(errorCard(file, message));
        continue;
      }
      const blob = await response.blob();
      const filename = decodeURIComponent(response.headers.get("x-filename") || "slika.jpg");
      const width = response.headers.get("x-width") || preset.width;
      const height = response.headers.get("x-height") || preset.height;
      const seconds = response.headers.get("x-seconds") || "";
      grid.append(resultCard(file, blob, filename, width, height, seconds));
    } catch (_) {
      let alive = false;
      try {
        alive = (await fetch("/api/status")).ok;
      } catch (_) {
        alive = false;
      }
      grid.append(errorCard(
        file,
        alive
          ? "Obrada je prekinuta. Sačekaj da se ova slika završi i pokušaj ponovo."
          : "Program se ugasio. Dvoklikni pokreni.command i probaj opet.",
      ));
    }
  }

  progress.textContent = "Gotovo. Preuzmi pojedinačno ili sve odjednom.";
  runButton.dataset.busy = "0";
  updateSpec();
  results.scrollIntoView({ behavior: "smooth", block: "start" });
});

zipButton.addEventListener("click", async () => {
  if (!jobId) return;
  zipButton.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${jobId}/zip`);
    if (!response.ok) return;
    saveBlob(await response.blob(), "pozadina.zip");
  } finally {
    zipButton.disabled = false;
  }
});

async function pollStatus() {
  try {
    const response = await fetch("/api/status");
    const payload = await response.json();
    statusEl.textContent = payload.message;
    statusEl.className = "status " + (payload.state === "ready" ? "ready" : payload.state === "error" ? "error" : "wait");
    if (payload.state === "ready" || payload.state === "error") return;
  } catch (_) {
    statusEl.textContent = "Program nije pokrenut. Dvoklikni pokreni.command.";
    statusEl.className = "status error";
  }
  setTimeout(pollStatus, 1500);
}

const sourceDir = document.querySelector("#source-dir");
const destDir = document.querySelector("#dest-dir");
const pickSource = document.querySelector("#pick-source");
const pickDest = document.querySelector("#pick-dest");
const runFolder = document.querySelector("#run-folder");
const stopFolder = document.querySelector("#stop-folder");
const folderProgress = document.querySelector("#folder-progress");
const folderErrors = document.querySelector("#folder-errors");
let folderJobId = "";
let folderWatchGen = 0;

async function chooseFolder(kind, input) {
  const previousPlaceholder = input.placeholder;
  input.placeholder = "Čekam izbor foldera…";
  try {
    const response = await fetch(`/api/pick-folder?kind=${kind}`, { method: "POST" });
    const payload = await response.json();
    if (payload.path) input.value = payload.path;
  } catch (_) {
    folderProgress.textContent = "Izbor foldera nije uspeo. Nalepi putanju ručno.";
  } finally {
    input.placeholder = previousPlaceholder;
  }
}

pickSource.addEventListener("click", () => chooseFolder("source", sourceDir));
pickDest.addEventListener("click", () => chooseFolder("dest", destDir));

function renderFolderErrors(errors) {
  folderErrors.innerHTML = "";
  for (const item of errors) {
    const line = document.createElement("li");
    line.textContent = `${item.file}: ${item.message}`;
    folderErrors.append(line);
  }
}

function folderMessage(payload) {
  if (payload.state === "running") {
    const left = Math.max(payload.total - payload.done, 0);
    let estimate = "";
    if (payload.done > 0 && left > 0) {
      const minutes = Math.max(1, Math.round((left * payload.elapsed) / payload.done / 60));
      estimate = ` Ostalo oko ${minutes} min.`;
    }
    const name = payload.current || "pripremam";
    return `Obrađujem ${Math.min(payload.done + 1, payload.total)} od ${payload.total}: ${name}.${estimate}`;
  }
  if (payload.state === "stopped") {
    return `Zaustavljeno posle ${payload.done} od ${payload.total}. Sačuvano ${payload.saved} gotovih slika.`;
  }
  return `Gotovo. Sačuvano ${payload.saved} slika u folderima instagram i kupujemprodajem, u ${payload.destination}.`;
}

function showRunningFolder(payload) {
  folderJobId = payload.id;
  if (payload.source) sourceDir.value = payload.source;
  if (payload.destination) destDir.value = payload.destination;
  runFolder.disabled = true;
  stopFolder.hidden = false;
  folderProgress.textContent = folderMessage(payload);
  renderFolderErrors(payload.errors || []);
}

async function resumeFolder() {
  try {
    const response = await fetch("/api/folder/active");
    if (!response.ok) return false;
    const payload = await response.json();
    if (!payload.id || payload.state !== "running") return false;
    showRunningFolder(payload);
    watchFolder();
    return true;
  } catch (_) {
    return false;
  }
}

async function watchFolder() {
  const gen = ++folderWatchGen;
  if (!folderJobId) return;
  const jobId = folderJobId;
  try {
    const response = await fetch(`/api/folder/${jobId}`);
    if (gen !== folderWatchGen) return;
    if (!response.ok) return;
    const payload = await response.json();
    folderProgress.textContent = folderMessage(payload);
    renderFolderErrors(payload.errors || []);
    if (payload.state === "running") {
      runFolder.disabled = true;
      stopFolder.hidden = false;
      setTimeout(() => {
        if (gen === folderWatchGen) watchFolder();
      }, 1000);
      return;
    }
  } catch (_) {
    if (gen !== folderWatchGen) return;
    folderProgress.textContent = "Veza sa programom je prekinuta.";
  }
  if (gen !== folderWatchGen) return;
  runFolder.disabled = false;
  stopFolder.hidden = true;
  folderJobId = "";
}

runFolder.addEventListener("click", async () => {
  if (folderJobId) return;
  runFolder.disabled = true;
  stopFolder.hidden = false;
  folderProgress.textContent = "Tražim slike u folderu…";
  folderErrors.innerHTML = "";
  try {
    const response = await fetch("/api/folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: sourceDir.value.trim(),
        destination: destDir.value.trim(),
      }),
    });
    const payload = await response.json();
    if (response.status === 409) {
      const attached = await resumeFolder();
      if (!attached) {
        folderProgress.textContent = payload.detail || "Obrada foldera je već u toku.";
        runFolder.disabled = false;
        stopFolder.hidden = true;
      }
      return;
    }
    if (!response.ok) {
      folderProgress.textContent = payload.detail || "Obrada foldera nije počela.";
      runFolder.disabled = false;
      stopFolder.hidden = true;
      return;
    }
    folderJobId = payload.id;
    folderProgress.textContent = `Nađeno ${payload.total} slika. Svaka ide u Instagram kvadrat i u KupujemProdajem. Prva traje oko 20 sekundi.`;
    watchFolder();
  } catch (_) {
    folderProgress.textContent = "Program nije pokrenut. Dvoklikni pokreni.command.";
    runFolder.disabled = false;
    stopFolder.hidden = true;
  }
});

stopFolder.addEventListener("click", async () => {
  if (!folderJobId) return;
  stopFolder.disabled = true;
  await fetch(`/api/folder/${folderJobId}/stop`, { method: "POST" });
  stopFolder.disabled = false;
});

applyPreset("kp");
pollStatus();
resumeFolder();
