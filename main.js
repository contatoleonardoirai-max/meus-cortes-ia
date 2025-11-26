const form = document.getElementById("clip-form");
const errorEl = document.getElementById("error");
const resultCard = document.getElementById("result-card");
const clipsContainer = document.getElementById("clips-container");

const backendURL = "http://127.0.0.1:8000";

const modeRadios = document.querySelectorAll('input[name="mode"]');
const urlFields = document.getElementById("url-fields");
const uploadFields = document.getElementById("upload-fields");

modeRadios.forEach((radio) => {
  radio.addEventListener("change", () => {
    if (radio.value === "url" && radio.checked) {
      urlFields.style.display = "block";
      uploadFields.style.display = "none";
    } else if (radio.value === "upload" && radio.checked) {
      urlFields.style.display = "none";
      uploadFields.style.display = "block";
    }
  });
});

function formatTime(seconds) {
  const m = String(Math.floor(seconds / 60)).padStart(2, "0");
  const s = String(Math.floor(seconds % 60)).padStart(2, "0");
  return `${m}:${s}`;
}

function renderClips(clips, mode, platform) {
  clipsContainer.innerHTML = "";

  clips.forEach((clip) => {
    const card = document.createElement("article");
    card.className = "clip-card";

    const thumb = document.createElement("div");
    thumb.className = "clip-card-thumb";
    thumb.style.backgroundImage =
      "url('https://placehold.co/600x300?text=Corte+" + clip.id + "')";

    const body = document.createElement("div");
    body.className = "clip-card-body";

    const title = document.createElement("h3");
    title.textContent = `Corte ${clip.id} (${mode})`;

    const timeInfo = document.createElement("p");
    timeInfo.textContent = `${formatTime(
      clip.start
    )} → ${formatTime(clip.end)} • ${platform}`;

    const link = document.createElement("a");
    link.href = backendURL + clip.downloadUrl;
    link.textContent = "Baixar MP4";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.style.fontSize = "0.8rem";

    body.appendChild(title);
    body.appendChild(timeInfo);
    body.appendChild(link);

    card.appendChild(thumb);
    card.appendChild(body);

    clipsContainer.appendChild(card);
  });

  resultCard.style.display = "block";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  errorEl.classList.add("hidden");
  errorEl.textContent = "";
  resultCard.style.display = "none";
  clipsContainer.innerHTML = "";

  const mode = document.querySelector('input[name="mode"]:checked').value;
  const clipsCount = Number(
    document.getElementById("clipsCount").value.trim()
  );
  const maxDuration = Number(
    document.getElementById("maxDuration").value.trim()
  );
  const platform = document.getElementById("platform").value;

  if (!clipsCount || clipsCount <= 0 || clipsCount > 20) {
    errorEl.textContent = "Quantidade de cortes deve ser entre 1 e 20.";
    errorEl.classList.remove("hidden");
    return;
  }

  if (!maxDuration || maxDuration < 5) {
    errorEl.textContent =
      "Duração máxima por corte deve ser de pelo menos 5 segundos.";
    errorEl.classList.remove("hidden");
    return;
  }

  let endpoint = "";
  let options = {};

  if (mode === "url") {
    const videoUrl = document.getElementById("videoUrl").value.trim();
    if (!videoUrl || !videoUrl.startsWith("http")) {
      errorEl.textContent =
        "Informe um link de vídeo válido (começando com http).";
      errorEl.classList.remove("hidden");
      return;
    }

    endpoint = `${backendURL}/api/generate-clips-from-url`;
    options = {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        videoUrl,
        clipsCount,
        maxDuration,
        platform,
      }),
    };
  } else {
    // upload
    const fileInput = document.getElementById("videoFile");
    const file = fileInput.files[0];

    if (!file) {
      errorEl.textContent = "Selecione um arquivo de vídeo para upload.";
      errorEl.classList.remove("hidden");
      return;
    }

    const formData = new FormData();
    formData.append("file", file);
    formData.append("clipsCount", String(clipsCount));
    formData.append("maxDuration", String(maxDuration));
    formData.append("platform", platform);

    endpoint = `${backendURL}/api/generate-clips-from-upload`;
    options = {
      method: "POST",
      body: formData,
    };
  }

  const submitBtn = form.querySelector("button[type='submit']");
  const originalText = submitBtn.textContent;
  submitBtn.disabled = true;
  submitBtn.textContent =
    mode === "url" ? "Baixando e cortando..." : "Enviando e cortando...";

  try {
    const res = await fetch(endpoint, options);
    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      const msg =
        data.detail ||
        data.error ||
        `Erro HTTP ${res.status} ao chamar backend.`;
      errorEl.textContent = msg;
      errorEl.classList.remove("hidden");
      console.error("Erro backend:", msg);
      return;
    }

    const clips = data.clips || [];
    if (!clips.length) {
      errorEl.textContent = "Nenhum corte foi gerado.";
      errorEl.classList.remove("hidden");
      return;
    }

    renderClips(clips, data.mode || mode, platform);
  } catch (err) {
    console.error(err);
    errorEl.textContent =
      "Erro ao conectar com o backend. Ele está rodando em 127.0.0.1:8000 e sem firewall bloqueando?";
    errorEl.classList.remove("hidden");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = originalText;
  }
});
