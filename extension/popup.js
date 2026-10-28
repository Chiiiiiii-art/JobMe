/**
 * JobMe Chrome Extension - Popup Logic
 */

let currentJobData = null;
let currentApiUrl = "http://127.0.0.1:8000";

// DOM Elements
const connStatus = document.getElementById("conn-status");
const connText = document.getElementById("conn-text");
const jobCard = document.getElementById("job-card");
const platformBadge = document.getElementById("platform-badge");
const companyName = document.getElementById("company-name");
const jobTitle = document.getElementById("job-title");

// Panels
const stateChecking = document.getElementById("state-checking");
const stateEvaluated = document.getElementById("state-evaluated");
const stateRejected = document.getElementById("state-rejected");
const stateNotEvaluated = document.getElementById("state-not-evaluated");
const stateEvaluating = document.getElementById("state-evaluating");
const stateNotJd = document.getElementById("state-not-jd");
const stateError = document.getElementById("state-error");

// Evaluated panel elements
const scoreCircle = document.getElementById("score-circle");
const scoreVal = document.getElementById("score-val");
const evalVerdict = document.getElementById("eval-verdict");
const tierTag = document.getElementById("tier-tag");
const fitTag = document.getElementById("fit-tag");
const dimensionsList = document.getElementById("dimensions-list");
const breakdownSummary = document.getElementById("breakdown-summary");

// Notice & Progress elements
const rejectedMsg = document.getElementById("rejected-msg");
const notJdMsg = document.getElementById("not-jd-msg");
const evalStatusText = document.getElementById("eval-status-text");
const errorTitle = document.getElementById("error-title");
const errorMsg = document.getElementById("error-msg");
const serverOfflineHelp = document.getElementById("server-offline-help");

// Buttons
const btnEvaluate = document.getElementById("btn-evaluate");
const btnReEvaluate = document.getElementById("btn-re-evaluate");
const btnForceEvaluate = document.getElementById("btn-force-evaluate");
const btnRetry = document.getElementById("btn-retry");
const btnSettingsToggle = document.getElementById("btn-settings-toggle");
const btnSettingsClose = document.getElementById("btn-settings-close");
const btnSaveSettings = document.getElementById("btn-save-settings");
const inputApiUrl = document.getElementById("input-api-url");
const settingsOverlay = document.getElementById("settings-overlay");

function hideAllStates() {
  stateChecking.classList.add("hidden");
  stateEvaluated.classList.add("hidden");
  stateRejected.classList.add("hidden");
  stateNotEvaluated.classList.add("hidden");
  stateEvaluating.classList.add("hidden");
  stateNotJd.classList.add("hidden");
  stateError.classList.add("hidden");
}

function showState(element) {
  hideAllStates();
  element.classList.remove("hidden");
}

function updateConnectionStatus(online, message) {
  connStatus.className = "status-badge " + (online ? "status-online" : "status-offline");
  connText.textContent = message || (online ? "本機就緒" : "伺服器離線");
  if (!online) {
    serverOfflineHelp.classList.remove("hidden");
  } else {
    serverOfflineHelp.classList.add("hidden");
  }
}

// Check Backend API Health
async function pingBackend() {
  try {
    const res = await fetch(`${currentApiUrl}/api/status`, { method: "GET" });
    if (res.ok) {
      const data = await res.json();
      updateConnectionStatus(true, "本機就緒");
      return true;
    }
  } catch (err) {
    console.warn("Backend ping failed:", err);
  }
  updateConnectionStatus(false, "伺服器離線");
  return false;
}

// Render Evaluated Result
function renderEvaluated(data) {
  const total = Number(data.total_score || 0).toFixed(1);
  scoreVal.textContent = total;

  // Score circle color classes
  scoreCircle.className = "score-circle";
  if (Number(total) >= 85) {
    scoreCircle.classList.add("score-pass");
  } else if (Number(total) >= 70) {
    scoreCircle.classList.add("score-medium");
  } else {
    scoreCircle.classList.add("score-fail");
  }

  // Verdict
  if (data.is_qualified) {
    evalVerdict.className = "verdict-tag verdict-pass";
    evalVerdict.textContent = "✅ 達標推薦";
  } else {
    evalVerdict.className = "verdict-tag verdict-fail";
    evalVerdict.textContent = "未達推薦門檻";
  }

  // Tier and Fit tags
  tierTag.textContent = data.company_tier || "Tier 未知";
  fitTag.textContent = data.fit_level ? `契合度 ${data.fit_level.toUpperCase()}` : "適配通過";

  // Dimension Bars
  dimensionsList.innerHTML = "";
  if (Array.isArray(data.dimensions) && data.dimensions.length > 0) {
    breakdownSummary.textContent = `共 ${data.dimensions.length} 維度`;
    data.dimensions.forEach(dim => {
      const score = Number(dim.score || 0).toFixed(1);
      const max = Number(dim.max || 1);
      const pct = Math.min(100, Math.max(0, (score / max) * 100));

      const item = document.createElement("div");
      item.className = "dimension-item";
      item.innerHTML = `
        <div class="dimension-info">
          <span class="dimension-name">${dim.name}</span>
          <span class="dimension-score">${score} <span style="font-size:10px;color:var(--text-muted);font-weight:normal;">/ ${max}</span></span>
        </div>
        <div class="progress-track">
          <div class="progress-bar-fill" style="width: ${pct}%;"></div>
        </div>
      `;
      dimensionsList.appendChild(item);
    });
  } else {
    breakdownSummary.textContent = "";
  }

  showState(stateEvaluated);
}

function checkIsJobUrl(url) {
  if (!url) return false;
  if (url.includes("104.com.tw")) return /\/job\/[a-zA-Z0-9]+/i.test(url);
  if (url.includes("cake.me") || url.includes("cakeresume.com")) return /\/companies\/[^/]+\/jobs\/[^/?#]+/i.test(url);
  if (url.includes("yourator.co")) return /\/(?:companies\/[^/]+\/)?jobs\/\d+/i.test(url);
  if (url.includes("1111.com.tw")) return /\/job\/\d+/i.test(url);
  if (url.includes("yes123.com.tw")) return /job_id=[a-zA-Z0-9_]+/i.test(url);
  return false;
}

function detectPlatformFromUrl(url) {
  if (!url) return "web";
  if (url.includes("104.com.tw")) return "104";
  if (url.includes("cake.me") || url.includes("cakeresume.com")) return "cake";
  if (url.includes("yourator.co")) return "yourator";
  if (url.includes("1111.com.tw")) return "1111";
  if (url.includes("yes123.com.tw")) return "yes123";
  return "web";
}

// Fetch existing check
async function checkCurrentJob() {
  showState(stateChecking);

  // Ping backend first
  const isOnline = await pingBackend();
  if (!isOnline) {
    errorTitle.textContent = "無法連線至 JobMe 伺服器";
    errorMsg.textContent = `請確認本機已執行命令：python -m jobme --serve (目前網址: ${currentApiUrl})`;
    showState(stateError);
    return;
  }

  // Query active tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url) {
    errorTitle.textContent = "無法讀取分頁網址";
    errorMsg.textContent = "請切換至求職網頁分頁後重試。";
    showState(stateError);
    return;
  }

  // Request extracted data from content script
  let contentScriptFailed = false;
  try {
    const response = await chrome.tabs.sendMessage(tab.id, { action: "extract_job_info" });
    if (response && response.success && response.data) {
      currentJobData = response.data;
    } else {
      contentScriptFailed = true;
    }
  } catch (e) {
    contentScriptFailed = true;
  }

  // If content script reported non-JD page
  if (currentJobData && currentJobData.isJobPage === false) {
    jobCard.classList.add("hidden");
    if (currentJobData.reason) {
      notJdMsg.textContent = currentJobData.reason;
    }
    showState(stateNotJd);
    return;
  }

  // If content script failed (e.g. non-supported website like google.com, youtube.com)
  if (!currentJobData || contentScriptFailed) {
    if (!checkIsJobUrl(tab.url)) {
      jobCard.classList.add("hidden");
      notJdMsg.textContent = "目前瀏覽的頁面不是支援平台之單一職缺詳情頁，無法進行 AI 評測。";
      showState(stateNotJd);
      return;
    }

    currentJobData = {
      isJobPage: true,
      url: tab.url,
      title: tab.title || "未知職缺",
      company: "未知企業",
      platform: detectPlatformFromUrl(tab.url),
      description: "",
    };
  }

  // Update Job Card UI
  jobCard.classList.remove("hidden");
  platformBadge.textContent = (currentJobData.platform || "WEB").toUpperCase();
  companyName.textContent = currentJobData.company || "求職職缺";
  jobTitle.textContent = currentJobData.title || tab.title;

  // Call /api/check
  try {
    const res = await fetch(`${currentApiUrl}/api/check?url=${encodeURIComponent(currentJobData.url)}`);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const checkRes = await res.json();

    if (checkRes.status === "not_a_job") {
      jobCard.classList.add("hidden");
      notJdMsg.textContent = checkRes.message || "目前網址非支援求職平台之單一職缺詳情頁。";
      showState(stateNotJd);
      return;
    }

    if (checkRes.status === "evaluated") {
      renderEvaluated(checkRes);
    } else if (checkRes.status === "stage1_rejected") {
      rejectedMsg.textContent = checkRes.message || "方向與履歷設定不吻合 (Stage 1)，已自動排除。";
      showState(stateRejected);
    } else {
      showState(stateNotEvaluated);
    }
  } catch (err) {
    errorTitle.textContent = "查詢職缺記錄失敗";
    errorMsg.textContent = err.message || "連線異常，請稍後再試。";
    showState(stateError);
  }
}

// Trigger evaluation
async function executeEvaluation(force = false) {
  if (!currentJobData || !currentJobData.url) {
    return;
  }

  showState(stateEvaluating);

  // Rotate progress hints
  const hints = [
    "正在分析職缺內容與履歷吻合度 (Stage 1)...",
    "查詢經濟部商工登記與上市櫃公開資料 (TWSE)...",
    "DeepSeek 權威性基準調查與企業分級錨定...",
    "Jev 雙語 Standard Fact Sheet 計算各維度期望值...",
    "儲存評分結果並檢查推播門檻...",
  ];
  let hintIdx = 0;
  evalStatusText.textContent = hints[0];
  const timer = setInterval(() => {
    hintIdx = (hintIdx + 1) % hints.length;
    evalStatusText.textContent = hints[hintIdx];
  }, 2500);

  try {
    const payload = {
      url: currentJobData.url,
      title: currentJobData.title,
      company: currentJobData.company,
      location: currentJobData.location,
      salary: currentJobData.salary,
      description: currentJobData.description,
      platform: currentJobData.platform,
      force: force,
    };

    const res = await fetch(`${currentApiUrl}/api/evaluate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    clearInterval(timer);

    if (!res.ok) {
      const errJson = await res.json().catch(() => ({}));
      throw new Error(errJson.detail || `HTTP 伺服器錯誤 ${res.status}`);
    }

    const result = await res.json();
    if (result.status === "not_a_job") {
      jobCard.classList.add("hidden");
      notJdMsg.textContent = result.message || "目前網址非支援求職平台之單一職缺詳情頁。";
      showState(stateNotJd);
    } else if (result.status === "evaluated") {
      renderEvaluated(result);
    } else if (result.status === "stage1_rejected") {
      rejectedMsg.textContent = result.message || "適配度未達標 (Stage 1)，履歷方向不符。";
      showState(stateRejected);
    } else {
      renderEvaluated(result);
    }
  } catch (err) {
    clearInterval(timer);
    errorTitle.textContent = "AI 評估執行異常";
    errorMsg.textContent = err.message || "評估過程發生錯誤，請檢查後端日誌或稍後重試。";
    showState(stateError);
  }
}

// Event Listeners
btnEvaluate.addEventListener("click", () => executeEvaluation(false));
btnReEvaluate.addEventListener("click", () => executeEvaluation(true));
btnForceEvaluate.addEventListener("click", () => executeEvaluation(true));
btnRetry.addEventListener("click", checkCurrentJob);

// Settings UI
btnSettingsToggle.addEventListener("click", () => {
  inputApiUrl.value = currentApiUrl;
  settingsOverlay.classList.remove("hidden");
});

btnSettingsClose.addEventListener("click", () => {
  settingsOverlay.classList.add("hidden");
});

btnSaveSettings.addEventListener("click", () => {
  const newUrl = inputApiUrl.value.trim().replace(/\/+$/, "");
  if (newUrl) {
    currentApiUrl = newUrl;
    chrome.storage.sync.set({ apiUrl: newUrl }, () => {
      settingsOverlay.classList.add("hidden");
      checkCurrentJob();
    });
  }
});

// Initialization
document.addEventListener("DOMContentLoaded", () => {
  chrome.storage.sync.get(["apiUrl"], result => {
    if (result && result.apiUrl) {
      currentApiUrl = result.apiUrl;
    }
    inputApiUrl.value = currentApiUrl;
    checkCurrentJob();
  });
});
