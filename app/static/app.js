const state = {
  videos: [],
  activeVideoId: null,
  selectedBatchIds: new Set(),
  saveTimer: null,
};

const elements = {
  sourceDirForm: document.getElementById("sourceDirForm"),
  sourceDirInput: document.getElementById("sourceDirInput"),
  pickSourceDirBtn: document.getElementById("pickSourceDirBtn"),
  saveSourceDirBtn: document.getElementById("saveSourceDirBtn"),
  openSourceDirBtn: document.getElementById("openSourceDirBtn"),
  outputDirForm: document.getElementById("outputDirForm"),
  outputDirInput: document.getElementById("outputDirInput"),
  pickOutputDirBtn: document.getElementById("pickOutputDirBtn"),
  saveOutputDirBtn: document.getElementById("saveOutputDirBtn"),
  refreshBtn: document.getElementById("refreshBtn"),
  clearListBtn: document.getElementById("clearListBtn"),
  listSizeSelect: document.getElementById("listSizeSelect"),
  videoList: document.getElementById("videoList"),
  activeTitle: document.getElementById("activeTitle"),
  previewVideo: document.getElementById("previewVideo"),
  currentTimeLabel: document.getElementById("currentTimeLabel"),
  durationLabel: document.getElementById("durationLabel"),
  startSlider: document.getElementById("startSlider"),
  endSlider: document.getElementById("endSlider"),
  startInput: document.getElementById("startInput"),
  endInput: document.getElementById("endInput"),
  zoomSlider: document.getElementById("zoomSlider"),
  zoomValue: document.getElementById("zoomValue"),
  zoomInBtn: document.getElementById("zoomInBtn"),
  zoomOutBtn: document.getElementById("zoomOutBtn"),
  timelineTrack: document.getElementById("timelineTrack"),
  timelineSelection: document.getElementById("timelineSelection"),
  timelineCursor: document.getElementById("timelineCursor"),
  setStartFromCurrent: document.getElementById("setStartFromCurrent"),
  setEndFromCurrent: document.getElementById("setEndFromCurrent"),
  saveSettingsBtn: document.getElementById("saveSettingsBtn"),
  outputNameInput: document.getElementById("outputNameInput"),
  singleTrimBtn: document.getElementById("singleTrimBtn"),
  openOutputDirBtn: document.getElementById("openOutputDirBtn"),
  singleResult: document.getElementById("singleResult"),
  batchTrimBtn: document.getElementById("batchTrimBtn"),
  batchStart: document.getElementById("batchStart"),
  batchEnd: document.getElementById("batchEnd"),
  batchResult: document.getElementById("batchResult"),
  commonBatchFields: document.getElementById("commonBatchFields"),
  statusBar: document.getElementById("statusBar"),
};

function setStatus(message, isError = false) {
  elements.statusBar.textContent = message || "";
  elements.statusBar.style.color = isError ? "#b33117" : "#0c3f57";
}

function formatTime(seconds) {
  const value = Number(seconds || 0);
  const whole = Math.floor(value);
  const ms = Math.round((value - whole) * 1000);
  const hour = Math.floor(whole / 3600).toString().padStart(2, "0");
  const minute = Math.floor((whole % 3600) / 60).toString().padStart(2, "0");
  const sec = Math.floor(whole % 60).toString().padStart(2, "0");
  return `${hour}:${minute}:${sec}.${ms.toString().padStart(3, "0")}`;
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function getActiveVideo() {
  return state.videos.find((item) => item.id === state.activeVideoId) || null;
}

function sortVideos() {
  state.videos.sort((a, b) => {
    const nameA = String(a.name || "");
    const nameB = String(b.name || "");
    return nameA.localeCompare(nameB, "zh-Hans-CN", { numeric: true, sensitivity: "base" });
  });
}

function upsertVideo(video) {
  const index = state.videos.findIndex((item) => item.id === video.id);
  if (index >= 0) {
    state.videos[index] = video;
  } else {
    state.videos.push(video);
  }
  sortVideos();
}

function applyListSize() {
  const value = Number(elements.listSizeSelect.value || 24);
  document.documentElement.style.setProperty("--list-rows", String(value));
}

function applyConfig(config) {
  if (!config) {
    return;
  }
  if (config.source_dir) {
    elements.sourceDirInput.value = config.source_dir;
  }
  if (config.output_dir) {
    elements.outputDirInput.value = config.output_dir;
  }
}

async function apiRequest(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.error || data.message || JSON.stringify(data);
    throw new Error(detail || `Request failed with status ${response.status}`);
  }
  return data;
}

function renderVideoList() {
  const fragment = document.createDocumentFragment();

  for (const video of state.videos) {
    const item = document.createElement("li");
    item.className = "video-item";
    if (video.id === state.activeVideoId) {
      item.classList.add("active");
    }

    const topRow = document.createElement("div");
    topRow.className = "video-row";

    const sourceTag = document.createElement("span");
    sourceTag.className = "video-source-tag";
    sourceTag.textContent = video.source_type === "source" ? "目录" : "上传";

    const title = document.createElement("strong");
    title.className = "video-title";
    title.textContent = video.name;
    title.title = video.name;

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selectedBatchIds.has(video.id);
    checkbox.title = "加入批量任务";
    checkbox.addEventListener("click", (event) => event.stopPropagation());
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        state.selectedBatchIds.add(video.id);
      } else {
        state.selectedBatchIds.delete(video.id);
      }
    });

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "delete-btn";
    deleteButton.textContent = "删";
    deleteButton.title = "删除该视频";
    deleteButton.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteSingleVideo(video).catch((error) => setStatus(error.message, true));
    });

    const actions = document.createElement("div");
    actions.className = "video-actions";
    actions.appendChild(checkbox);
    actions.appendChild(deleteButton);

    topRow.appendChild(sourceTag);
    topRow.appendChild(title);
    topRow.appendChild(actions);

    const meta = document.createElement("div");
    meta.className = "video-meta";
    const dimensionText = video.width && video.height ? `${video.width}x${video.height}` : "待读取分辨率";
    meta.textContent = `${formatTime(video.duration)} | ${dimensionText}`;

    item.appendChild(topRow);
    item.appendChild(meta);
    item.addEventListener("click", () => {
      activateVideo(video.id).catch((error) => setStatus(error.message, true));
    });
    fragment.appendChild(item);
  }

  elements.videoList.innerHTML = "";
  elements.videoList.appendChild(fragment);
}

function updateTimeline() {
  const video = getActiveVideo();
  if (!video) {
    elements.timelineSelection.style.left = "0%";
    elements.timelineSelection.style.width = "0%";
    elements.timelineCursor.style.left = "0%";
    return;
  }

  const duration = Number(video.duration || 0);
  if (duration <= 0) {
    elements.timelineSelection.style.left = "0%";
    elements.timelineSelection.style.width = "0%";
    elements.timelineCursor.style.left = "0%";
    return;
  }

  const start = Number(elements.startInput.value || 0);
  const end = Number(elements.endInput.value || 0);
  const current = Number(elements.previewVideo.currentTime || 0);

  const left = (start / duration) * 100;
  const width = ((end - start) / duration) * 100;
  const cursor = (current / duration) * 100;

  elements.timelineSelection.style.left = `${clamp(left, 0, 100)}%`;
  elements.timelineSelection.style.width = `${clamp(width, 0, 100)}%`;
  elements.timelineCursor.style.left = `${clamp(cursor, 0, 100)}%`;
}

function applyZoom(zoomValue, saveMode = "queue") {
  const zoom = clamp(Number(zoomValue || 1), 1, 20);
  elements.zoomSlider.value = zoom;
  elements.zoomValue.textContent = `${zoom.toFixed(1)}x`;
  elements.timelineTrack.style.width = `${zoom * 100}%`;

  if (saveMode === "immediate") {
    saveSettingsNow().catch((error) => setStatus(error.message, true));
  } else if (saveMode === "queue") {
    queueSaveSettings();
  }
}

function syncControlsFromActiveVideo() {
  const video = getActiveVideo();
  if (!video) {
    elements.activeTitle.textContent = "未选择视频";
    elements.previewVideo.removeAttribute("src");
    elements.previewVideo.load();
    elements.startSlider.max = "0";
    elements.endSlider.max = "0";
    elements.startInput.value = "0";
    elements.endInput.value = "0";
    elements.durationLabel.textContent = "总长: 00:00:00.000";
    elements.currentTimeLabel.textContent = "当前: 00:00:00.000";
    updateTimeline();
    return;
  }

  const duration = Number(video.duration || 0);
  const settings = video.settings || {};
  const start = clamp(Number(settings.start ?? 0), 0, Math.max(duration, 0));
  const endDefault = duration || 0;
  const end = clamp(Number(settings.end ?? endDefault), 0, Math.max(duration, 0));

  elements.activeTitle.textContent = video.name;
  elements.previewVideo.src = `/api/videos/${video.id}/preview?ts=${Date.now()}`;

  elements.startSlider.min = "0";
  elements.endSlider.min = "0";
  elements.startSlider.max = String(Math.max(duration, 0));
  elements.endSlider.max = String(Math.max(duration, 0));
  elements.startSlider.step = "0.01";
  elements.endSlider.step = "0.01";

  elements.startSlider.value = String(start);
  elements.endSlider.value = String(Math.max(start + 0.01, end));
  elements.startInput.value = String(start.toFixed(2));
  elements.endInput.value = String(Math.max(start + 0.01, end).toFixed(2));
  elements.durationLabel.textContent = `总长: ${formatTime(duration)}`;

  const zoom = Number(settings.zoom ?? 1);
  applyZoom(zoom, "none");
  elements.batchStart.value = String(start.toFixed(2));
  elements.batchEnd.value = String(Math.max(start + 0.01, end).toFixed(2));
  updateTimeline();
}

async function fetchVideoDetail(videoId) {
  const data = await apiRequest(`/api/videos/${videoId}`);
  upsertVideo(data.video);
  renderVideoList();
  return data.video;
}

async function loadVideos(preferredVideoId = null) {
  const data = await apiRequest("/api/videos");
  state.videos = data.videos || [];
  sortVideos();

  if (state.videos.length === 0) {
    state.activeVideoId = null;
  } else if (preferredVideoId && state.videos.some((v) => v.id === preferredVideoId)) {
    state.activeVideoId = preferredVideoId;
  } else if (!state.activeVideoId || !state.videos.some((v) => v.id === state.activeVideoId)) {
    state.activeVideoId = state.videos[0].id;
  }

  const existing = new Set(state.videos.map((item) => item.id));
  for (const id of Array.from(state.selectedBatchIds)) {
    if (!existing.has(id)) {
      state.selectedBatchIds.delete(id);
    }
  }

  renderVideoList();

  if (state.activeVideoId) {
    await fetchVideoDetail(state.activeVideoId);
  }

  syncControlsFromActiveVideo();
}

async function loadConfig() {
  const config = await apiRequest("/api/config");
  applyConfig(config);
}

async function updateSourceDir(sourceDir) {
  const config = await apiRequest("/api/config/source-dir", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_dir: sourceDir }),
  });
  applyConfig(config);
  await loadVideos();
}

async function updateOutputDir(outputDir) {
  const config = await apiRequest("/api/config/output-dir", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ output_dir: outputDir }),
  });
  applyConfig(config);
}

async function activateVideo(videoId) {
  state.activeVideoId = videoId;
  renderVideoList();
  await fetchVideoDetail(videoId);
  syncControlsFromActiveVideo();
}

async function deleteSingleVideo(video) {
  const confirmed = window.confirm(`删除视频记录：${video.name} ？`);
  if (!confirmed) {
    return;
  }

  await apiRequest(`/api/videos/${video.id}`, { method: "DELETE" });

  state.selectedBatchIds.delete(video.id);
  state.videos = state.videos.filter((item) => item.id !== video.id);

  if (state.activeVideoId === video.id) {
    state.activeVideoId = state.videos.length > 0 ? state.videos[0].id : null;
  }

  renderVideoList();
  syncControlsFromActiveVideo();
  if (state.activeVideoId) {
    await fetchVideoDetail(state.activeVideoId);
    syncControlsFromActiveVideo();
  }
  setStatus(`已删除：${video.name}`);
}

function normalizeRange(source = "input") {
  const video = getActiveVideo();
  if (!video) {
    return;
  }

  const duration = Number(video.duration || 0);
  if (duration <= 0) {
    elements.startInput.value = "0";
    elements.endInput.value = "0";
    elements.startSlider.value = "0";
    elements.endSlider.value = "0";
    updateTimeline();
    return;
  }

  const minGap = Math.max(0.01, duration / 10000);

  let start = Number(elements.startInput.value || 0);
  let end = Number(elements.endInput.value || 0);

  start = clamp(start, 0, duration);
  end = clamp(end, 0, duration);

  if (end <= start) {
    if (source.startsWith("start")) {
      end = clamp(start + minGap, minGap, duration);
    } else {
      start = clamp(end - minGap, 0, Math.max(0, duration - minGap));
    }
  }

  elements.startInput.value = start.toFixed(2);
  elements.endInput.value = end.toFixed(2);
  elements.startSlider.value = String(start);
  elements.endSlider.value = String(end);

  if (!Number.isNaN(elements.previewVideo.currentTime)) {
    if (elements.previewVideo.currentTime < start || elements.previewVideo.currentTime > end) {
      elements.previewVideo.currentTime = start;
    }
  }

  updateTimeline();
}

function queueSaveSettings() {
  const video = getActiveVideo();
  if (!video || Number(video.duration || 0) <= 0) {
    return;
  }

  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => {
    saveSettingsNow().catch((error) => setStatus(error.message, true));
  }, 450);
}

async function saveSettingsNow() {
  const video = getActiveVideo();
  if (!video) {
    return;
  }

  const duration = Number(video.duration || 0);
  const start = Number(elements.startInput.value || 0);
  const end = Number(elements.endInput.value || 0);
  if (duration <= 0 || end <= start) {
    return;
  }

  const payload = {
    start,
    end,
    zoom: Number(elements.zoomSlider.value || 1),
  };

  const data = await apiRequest(`/api/videos/${video.id}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  upsertVideo(data.video);
  setStatus("已保存当前视频剪裁设置");
}

async function runSingleTrim() {
  const video = getActiveVideo();
  if (!video) {
    setStatus("请先选择一个视频", true);
    return;
  }

  const payload = {
    video_id: video.id,
    start: Number(elements.startInput.value || 0),
    end: Number(elements.endInput.value || 0),
    output_name: elements.outputNameInput.value.trim() || null,
  };

  const data = await apiRequest("/api/trim/single", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const result = data.result;
  elements.singleResult.textContent = JSON.stringify(result, null, 2);
  setStatus(`单视频剪裁完成: ${result.output_name}`);
  await loadVideos(video.id);
}

async function runBatchTrim() {
  const selectedIds = Array.from(state.selectedBatchIds);
  if (selectedIds.length === 0) {
    setStatus("请先勾选要批量处理的视频", true);
    return;
  }

  const mode = document.querySelector("input[name='batchMode']:checked").value;
  let payload;

  if (mode === "common") {
    payload = {
      mode,
      video_ids: selectedIds,
      start: Number(elements.batchStart.value || 0),
      end: Number(elements.batchEnd.value || 0),
    };
  } else {
    payload = {
      mode,
      jobs: selectedIds.map((videoId) => {
        const video = state.videos.find((item) => item.id === videoId);
        const settings = (video && video.settings) || {};
        return {
          video_id: videoId,
          start: Number(settings.start ?? 0),
          end: Number(settings.end ?? video.duration ?? 0),
        };
      }),
    };
  }

  const data = await apiRequest("/api/trim/batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  elements.batchResult.textContent = JSON.stringify(data, null, 2);
  setStatus(`批量剪裁完成: 成功 ${data.success_count}，失败 ${data.failed_count}`);
  await loadVideos(state.activeVideoId);
}

function bindEvents() {
  elements.sourceDirForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const sourceDir = elements.sourceDirInput.value.trim();
    if (!sourceDir) {
      setStatus("请先输入原视频目录", true);
      return;
    }

    try {
      await updateSourceDir(sourceDir);
      setStatus("原视频目录已更新并完成读取");
    } catch (error) {
      setStatus(`设置目录失败: ${error.message}`, true);
    }
  });

  elements.pickSourceDirBtn.addEventListener("click", async () => {
    try {
      const picked = await apiRequest("/api/config/source-dir/pick", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initial_dir: elements.sourceDirInput.value.trim() || null }),
      });
      if (picked.cancelled) {
        setStatus("已取消选择原视频目录");
        return;
      }
      elements.sourceDirInput.value = picked.source_dir || elements.sourceDirInput.value;
      await updateSourceDir(elements.sourceDirInput.value.trim());
      setStatus("原视频目录已更新并完成读取");
    } catch (error) {
      setStatus(`选择目录失败: ${error.message}`, true);
    }
  });

  elements.openSourceDirBtn.addEventListener("click", async () => {
    try {
      await apiRequest("/api/source/open", { method: "POST" });
      setStatus("已打开原视频目录");
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  elements.outputDirForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const outputDir = elements.outputDirInput.value.trim();
    if (!outputDir) {
      setStatus("请先输入输出目录", true);
      return;
    }
    try {
      await updateOutputDir(outputDir);
      setStatus("输出目录已更新");
    } catch (error) {
      setStatus(`设置输出目录失败: ${error.message}`, true);
    }
  });

  elements.pickOutputDirBtn.addEventListener("click", async () => {
    try {
      const picked = await apiRequest("/api/config/output-dir/pick", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initial_dir: elements.outputDirInput.value.trim() || null }),
      });
      if (picked.cancelled) {
        setStatus("已取消选择输出目录");
        return;
      }
      elements.outputDirInput.value = picked.output_dir || elements.outputDirInput.value;
      await updateOutputDir(elements.outputDirInput.value.trim());
      setStatus("输出目录已更新");
    } catch (error) {
      setStatus(`选择目录失败: ${error.message}`, true);
    }
  });

  elements.refreshBtn.addEventListener("click", () => {
    loadVideos(state.activeVideoId)
      .then(() => setStatus("视频列表已刷新（已读取当前原视频目录）"))
      .catch((error) => setStatus(error.message, true));
  });

  elements.clearListBtn.addEventListener("click", async () => {
    const confirmed = window.confirm("清空当前视频列表缓存？不会删除 原视频 或 新视频 目录中的真实文件。");
    if (!confirmed) {
      return;
    }

    try {
      await apiRequest("/api/videos/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "all" }),
      });
      state.videos = [];
      state.activeVideoId = null;
      state.selectedBatchIds.clear();
      renderVideoList();
      syncControlsFromActiveVideo();
      setStatus("视频列表已清空");
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  elements.listSizeSelect.addEventListener("change", applyListSize);

  elements.startSlider.addEventListener("input", () => {
    elements.startInput.value = Number(elements.startSlider.value).toFixed(2);
    normalizeRange("startSlider");
    queueSaveSettings();
  });

  elements.endSlider.addEventListener("input", () => {
    elements.endInput.value = Number(elements.endSlider.value).toFixed(2);
    normalizeRange("endSlider");
    queueSaveSettings();
  });

  elements.startInput.addEventListener("input", () => {
    normalizeRange("startInput");
    queueSaveSettings();
  });

  elements.endInput.addEventListener("input", () => {
    normalizeRange("endInput");
    queueSaveSettings();
  });

  elements.zoomSlider.addEventListener("input", () => {
    applyZoom(elements.zoomSlider.value, "queue");
  });

  elements.zoomInBtn.addEventListener("click", () => {
    applyZoom(Number(elements.zoomSlider.value) + 0.5, "immediate");
  });

  elements.zoomOutBtn.addEventListener("click", () => {
    applyZoom(Number(elements.zoomSlider.value) - 0.5, "immediate");
  });

  elements.timelineTrack.addEventListener("click", (event) => {
    const video = getActiveVideo();
    if (!video || !video.duration) {
      return;
    }
    const ratio = clamp(event.offsetX / elements.timelineTrack.clientWidth, 0, 1);
    elements.previewVideo.currentTime = ratio * Number(video.duration);
    updateTimeline();
  });

  elements.previewVideo.addEventListener("timeupdate", () => {
    elements.currentTimeLabel.textContent = `当前: ${formatTime(elements.previewVideo.currentTime || 0)}`;
    updateTimeline();
  });

  elements.previewVideo.addEventListener("loadedmetadata", () => {
    const video = getActiveVideo();
    if (video) {
      elements.durationLabel.textContent = `总长: ${formatTime(video.duration)}`;
    }
    updateTimeline();
  });

  elements.setStartFromCurrent.addEventListener("click", () => {
    elements.startInput.value = Number(elements.previewVideo.currentTime || 0).toFixed(2);
    normalizeRange("startButton");
    queueSaveSettings();
  });

  elements.setEndFromCurrent.addEventListener("click", () => {
    elements.endInput.value = Number(elements.previewVideo.currentTime || 0).toFixed(2);
    normalizeRange("endButton");
    queueSaveSettings();
  });

  elements.saveSettingsBtn.addEventListener("click", () => {
    saveSettingsNow().catch((error) => setStatus(error.message, true));
  });

  elements.singleTrimBtn.addEventListener("click", () => {
    runSingleTrim().catch((error) => setStatus(error.message, true));
  });

  elements.openOutputDirBtn.addEventListener("click", async () => {
    try {
      await apiRequest("/api/outputs/open", { method: "POST" });
      setStatus("已打开输出目录：新视频");
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  document.querySelectorAll("input[name='batchMode']").forEach((radio) => {
    radio.addEventListener("change", () => {
      const mode = document.querySelector("input[name='batchMode']:checked").value;
      elements.commonBatchFields.style.display = mode === "common" ? "grid" : "none";
    });
  });

  elements.batchTrimBtn.addEventListener("click", () => {
    runBatchTrim().catch((error) => setStatus(error.message, true));
  });

  const clearCacheOnExit = () => {
    try {
      const payload = new Blob([JSON.stringify({ mode: "cache" })], { type: "application/json" });
      navigator.sendBeacon("/api/videos/clear", payload);
    } catch (_error) {
      // ignore exit cleanup failures
    }
  };

  window.addEventListener("beforeunload", clearCacheOnExit);
  window.addEventListener("pagehide", clearCacheOnExit);
}

async function init() {
  bindEvents();
  applyListSize();
  try {
    await loadConfig();
    await loadVideos();
    setStatus("就绪：已根据原视频目录读取文件");
  } catch (error) {
    setStatus(error.message, true);
  }
}

init();
