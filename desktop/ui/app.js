const endpoint = "http://127.0.0.1:18777";
const setup = document.querySelector("#setup");
const frame = document.querySelector("#app");
const checks = document.querySelector("#checks");
const title = document.querySelector("#title");
const lead = document.querySelector("#lead");
const actions = document.querySelector("#actions");
const dataDir = document.querySelector("#data-dir");
const providerButtons = [...document.querySelectorAll(".provider")];
let lastHealth = null;

function openApp() {
  localStorage.setItem("liveMtgSetupSeen", "1");
  frame.src = `${endpoint}/?desktop=1`;
  frame.hidden = false;
  setup.hidden = true;
}

function render(health) {
  lastHealth = health;
  providerButtons.forEach(button => {
    const selected = button.dataset.provider === health.aiProvider;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  checks.innerHTML = health.checks.map(item => `
    <div class="check ${item.ok ? "ok" : "ng"}">
      <span class="icon">${item.ok ? "✓" : "!"}</span>
      <div><strong>${item.label}</strong><small>${item.ok ? "利用できます" : item.help}</small></div>
    </div>`).join("");
  dataDir.textContent = `会議データの保存先：${health.dataDir}`;
  actions.classList.remove("hidden");
  if (health.ok) {
    title.textContent = "準備ができました";
    lead.textContent = "必要な機能をすべて確認できました。LiveMTGを開きます。";
    document.querySelector("#continue").textContent = "LiveMTGを開く";
    if (localStorage.getItem("liveMtgSetupSeen")) setTimeout(openApp, 350);
  } else {
    title.textContent = "最初に3項目だけ確認してください";
    lead.textContent = "画面の閲覧はできますが、録音解析には不足している項目の準備が必要です。";
  }
}

async function selectProvider(provider) {
  providerButtons.forEach(button => button.disabled = true);
  title.textContent = "AIを切り替えています";
  lead.textContent = "選択を保存して、利用できる状態か確認します。";
  try {
    const response = await fetch(`${endpoint}/api/settings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ aiProvider: provider })
    });
    if (!response.ok) throw new Error("save failed");
    localStorage.setItem("liveMtgProviderChosen", "1");
    await probe();
  } catch {
    title.textContent = "AIを切り替えられませんでした";
    lead.textContent = "もう一度選択してください。設定や会議データは失われません。";
  } finally {
    providerButtons.forEach(button => button.disabled = false);
  }
}

async function probe(attempt = 0) {
  try {
    const response = await fetch(`${endpoint}/api/desktop-health`, { cache: "no-store" });
    if (!response.ok) throw new Error("backend unavailable");
    render(await response.json());
  } catch (error) {
    if (attempt < 24) return setTimeout(() => probe(attempt + 1), 500);
    checks.innerHTML = '<div class="check ng"><span class="icon">!</span><div><strong>バックエンドを起動できません</strong><small>アプリを終了して、もう一度起動してください。</small></div></div>';
    title.textContent = "起動に失敗しました";
    lead.textContent = "バックエンドから応答がありません。別のLiveMTGが起動中でないか確認してください。";
    actions.classList.remove("hidden");
    document.querySelector("#continue").hidden = true;
  }
}

document.querySelector("#retry").addEventListener("click", () => probe());
document.querySelector("#continue").addEventListener("click", openApp);
providerButtons.forEach(button => button.addEventListener("click", () => selectProvider(button.dataset.provider)));
probe();
