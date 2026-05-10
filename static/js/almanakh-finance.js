/**
 * المركز المالي — تحديث KPI، رسم بياني تأجيل، رؤى، مخزون، شات مانوس.
 */
(function () {
  const E = typeof window.MM_FIN_ENDPOINTS !== "undefined" ? window.MM_FIN_ENDPOINTS : {};
  let branchChartInstance = null;

  function nf(n) {
    try {
      return new Intl.NumberFormat("ar-SA", { maximumFractionDigits: 0 }).format(Number(n || 0));
    } catch (e) {
      return String(n);
    }
  }

  function branchRowsFromPayload(m) {
    const bb = (m && m.branches_breakdown) || [];
    if (bb.length) return bb;
    return Array.isArray(window.MM_FIN_SSR_BRANCHES) ? window.MM_FIN_SSR_BRANCHES : [];
  }

  function applyMetrics(m) {
    if (!m) return;
    const elSales = document.getElementById("mnKpiSales");
    const elTx = document.getElementById("mnKpiTx");
    const elMargin = document.getElementById("mnKpiMargin");
    const elSrc = document.getElementById("mnKpiSrc");
    if (elSales) elSales.textContent = nf(m.today_sales);
    if (elTx) elTx.textContent = nf(m.transaction_count);
    if (elMargin) elMargin.textContent = String(m.profit_margin_estimate_pct ?? "") + "%";
    if (elSrc) elSrc.textContent = m.mode === "remote" ? "Amazon API" : m.mode === "internal_fallback" ? "داخلية" : String(m.mode || "—");

    renderBranchChart(branchRowsFromPayload(m));
    const lazyTag = document.getElementById("mnChartLazyTag");
    if (lazyTag) lazyTag.textContent = "محدَّث";
  }

  function renderBranchChart(branchRows) {
    const canvas = document.getElementById("mnBranchChart");
    if (!canvas || typeof Chart === "undefined") return;

    const labels = branchRows.map((b) => b.branch_name || "?");
    const sales = branchRows.map((b) => Number(b.estimated_sales_month) || 0);
    const inquiries = branchRows.map((b) => Number(b.inquiry_total) || 0);

    const data = {
      labels,
      datasets: [
        {
          label: "مبيعات (تقدير/شهر)",
          data: sales,
          borderColor: "#d4af37",
          backgroundColor: "rgba(212, 175, 55, 0.28)",
          borderWidth: 2,
          tension: 0.25,
          fill: false,
        },
        {
          label: "استفسارات",
          data: inquiries,
          borderColor: "#38bdf8",
          backgroundColor: "rgba(56, 189, 248, 0.22)",
          borderWidth: 2,
          tension: 0.25,
          fill: false,
        },
      ],
    };

    if (branchChartInstance) branchChartInstance.destroy();

    branchChartInstance = new Chart(canvas, {
      type: "line",
      data,
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { labels: { color: "#cfd7e8" } } },
        scales: {
          x: {
            ticks: { color: "#8b96ab", maxRotation: 45 },
            grid: { color: "rgba(255,255,255,.06)" },
          },
          y: {
            ticks: { color: "#8b96ab" },
            grid: { color: "rgba(255,255,255,.06)" },
          },
        },
      },
    });
  }

  function fetchJson(url) {
    return fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } }).then((r) => r.json());
  }

  function loadMetricsDeferred() {
    if (!E.metricsJson) return;
    fetchJson(E.metricsJson)
      .then((j) => {
        if (!j.ok) return;
        applyMetrics(j.metrics);
      })
      .catch(() => {});
  }

  async function loadInsights() {
    const box = document.getElementById("mnInsightsBody");
    if (!box || !E.insightsJson) return;
    box.textContent = "جاري قراءة المقاييس ومانوس…";
    try {
      const j = await fetchJson(E.insightsJson);
      if (!j.ok) box.textContent = j.error ? String(j.error) : "تعذّر جلب الرؤى.";
      else box.textContent = j.text || "—";
    } catch (_) {
      box.textContent = "خطأ شبكة أو انقطاع.";
    }
  }

  function loadInventory() {
    const el = document.getElementById("mnInventoryBody");
    if (!el || !E.inventoryJson) return;
    fetchJson(E.inventoryJson)
      .then((j) => {
        if (!j.ok) return;
        const sig = j.inventory_signal || {};
        const n = sig.products_registered;
        el.innerHTML =
          n != null ? `المنتجات المُسَجَّلة (مؤشر): <strong>${nf(n)}</strong>` : "—";
      })
      .catch(() => {});
  }

  /** Lazy: رسم ومقاييس عند ظهور البطاقة */
  function whenVisible(el, fn) {
    if (!el) return;
    if (!("IntersectionObserver" in window)) {
      fn();
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((en) => {
          if (!en.isIntersecting) return;
          io.disconnect();
          fn();
        });
      },
      { rootMargin: "80px", threshold: 0.05 }
    );
    io.observe(el);
  }

  function setupChat() {
    const launcher = document.getElementById("mnChatLauncher");
    const panel = document.getElementById("mnChatPanel");
    const close = document.getElementById("mnChatClose");
    const send = document.getElementById("mnChatSend");
    const inp = document.getElementById("mnChatInput");
    const msgs = document.getElementById("mnChatMsgs");
    if (!launcher || !panel || !send || !inp || !msgs || !E.chatJson) return;

    const toggle = () => panel.classList.toggle("d-none");
    launcher.addEventListener("click", toggle);
    if (close) close.addEventListener("click", toggle);
    launcher.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        toggle();
      }
    });

    function addMsg(text, isUser) {
      const div = document.createElement("div");
      div.className = "mm-fin-msg" + (isUser ? " mm-fin-msg--user" : "");
      div.textContent = text;
      msgs.appendChild(div);
      msgs.scrollTop = msgs.scrollHeight;
    }

    send.addEventListener("click", async () => {
      const msg = inp.value.trim();
      if (!msg) return;
      addMsg(msg, true);
      inp.value = "";
      try {
        const r = await fetch(E.chatJson, {
          method: "POST",
          credentials: "same-origin",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({ message: msg }),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || !j.ok) addMsg(j.error || "تعذّر الرد.", false);
        else addMsg(j.reply || "—", false);
      } catch (_) {
        addMsg("خطأ شبكة.", false);
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    const chartWrap = document.querySelector(".mm-fin-card-chart") || document.getElementById("mnBranchChart");
    whenVisible(chartWrap, function () {
      loadMetricsDeferred();
    });

    const insightsBox = document.querySelector(".mm-fin-manukh");
    whenVisible(insightsBox, function () {
      loadInsights();
    });

    whenVisible(document.getElementById("mnInventoryBody"), loadInventory);

    document.getElementById("mnRefreshInsights")?.addEventListener("click", loadInsights);

    const ssr = Array.isArray(window.MM_FIN_SSR_BRANCHES) ? window.MM_FIN_SSR_BRANCHES : [];
    renderBranchChart(ssr);

    setupChat();
  });
})();
