/**
 * المركز المالي — مجمع العائلة
 * --------------------------------------------------
 * يقرأ السياق المالي الكامل من السيرفر (SSR) ثم يحدّثه بنداءات JSON.
 * يرسم Chart.js للمبيعات والاستفسارات، ويدير محادثة "مانوس".
 */
(function () {
  const E = (typeof window.MM_FIN_ENDPOINTS !== "undefined") ? window.MM_FIN_ENDPOINTS : {};
  const SSR = (typeof window.MM_FIN_SSR_METRICS !== "undefined") ? window.MM_FIN_SSR_METRICS : {};
  let branchChartInstance = null;

  // ── أدوات تنسيق ──
  function nf(n, opts) {
    try {
      return new Intl.NumberFormat("ar-SA", Object.assign({ maximumFractionDigits: 0 }, opts || {})).format(Number(n || 0));
    } catch (e) {
      return String(n);
    }
  }

  function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }

  // ── KPIs ──
  function applyKpis(m) {
    if (!m) return;
    setText("mnKpiSales", nf(m.today_sales));
    setText("mnKpiTx", nf(m.transaction_count));
    setText("mnKpiSrc", m.mode === "remote" ? "ERP / API" : m.mode === "internal_fallback" ? "تقدير داخلي" : String(m.mode || "—"));
    const k = m.kpis || {};
    setText("mnKpiGross", nf(k.gross_profit));
    setText("mnKpiOpex", nf(k.operating_expenses));
    setText("mnKpiNet", nf(k.net_margin_value));
    setText("mnKpiTicket", nf(k.avg_ticket));
  }

  // ── Chart.js: مبيعات vs استفسارات لكل فرع ──
  function renderBranchChart(branchRows) {
    const canvas = document.getElementById("mnBranchChart");
    if (!canvas || typeof Chart === "undefined") return;
    if (!Array.isArray(branchRows) || branchRows.length === 0) {
      branchRows = [];
    }

    const labels = branchRows.map((b) => b.branch_name || "?");
    const sales = branchRows.map((b) => Number(b.estimated_sales_month) || 0);
    const inquiries = branchRows.map((b) => Number(b.inquiry_total) || 0);

    if (branchChartInstance) branchChartInstance.destroy();

    branchChartInstance = new Chart(canvas, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "مبيعات (تقدير شهري)",
            data: sales,
            backgroundColor: "rgba(201, 162, 39, 0.78)",
            borderColor: "#8a6a10",
            borderWidth: 1,
            yAxisID: "y",
          },
          {
            label: "استفسارات العملاء",
            data: inquiries,
            backgroundColor: "rgba(15, 81, 50, 0.7)",
            borderColor: "#0f5132",
            borderWidth: 1,
            yAxisID: "y1",
            type: "line",
            tension: 0.3,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { position: "bottom", labels: { color: "#0f5132", boxWidth: 14 } },
          tooltip: {
            backgroundColor: "#0f5132",
            titleColor: "#fff",
            bodyColor: "#fafaf0",
            cornerRadius: 8,
          },
        },
        scales: {
          x: {
            ticks: { color: "#3a3a3a", autoSkip: false, maxRotation: 35, minRotation: 0 },
            grid: { color: "rgba(0,0,0,.05)" },
          },
          y: {
            position: "right",
            ticks: { color: "#8a6a10" },
            grid: { color: "rgba(0,0,0,.05)" },
            title: { display: true, text: "المبيعات", color: "#8a6a10" },
          },
          y1: {
            position: "left",
            ticks: { color: "#0f5132" },
            grid: { display: false },
            title: { display: true, text: "الاستفسارات", color: "#0f5132" },
          },
        },
      },
    });
  }

  // ── شبكة ──
  function fetchJson(url, opts) {
    return fetch(url, Object.assign({ credentials: "same-origin", headers: { Accept: "application/json" } }, opts || {})).then((r) => r.json());
  }

  function loadMetricsDeferred() {
    if (!E.metricsJson) return;
    fetchJson(E.metricsJson)
      .then((j) => {
        if (!j.ok) return;
        applyKpis(j.metrics);
        renderBranchChart((j.metrics && j.metrics.branches_breakdown) || []);
      })
      .catch(() => {});
  }

  async function loadInsights() {
    const box = document.getElementById("mnInsightsBody");
    if (!box || !E.insightsJson) return;
    box.textContent = "جاري قراءة المقاييس ومانوس…";
    try {
      const j = await fetchJson(E.insightsJson);
      if (!j.ok) {
        box.textContent = j.error ? String(j.error) : "تعذّر جلب الرؤى.";
      } else {
        box.textContent = j.text || "—";
      }
    } catch (_) {
      box.textContent = "خطأ شبكة أو انقطاع.";
    }
  }

  // ── شات مانوس ──
  function setupChat() {
    const launcher = document.getElementById("mnChatLauncher");
    const panel = document.getElementById("mnChatPanel");
    const close = document.getElementById("mnChatClose");
    const send = document.getElementById("mnChatSend");
    const inp = document.getElementById("mnChatInput");
    const msgs = document.getElementById("mnChatMsgs");
    if (!launcher || !panel || !send || !inp || !msgs || !E.chatJson) return;

    function show(open) {
      panel.classList.toggle("d-none", !open);
      if (open) {
        setTimeout(() => inp.focus(), 50);
      }
    }
    function toggle() { show(panel.classList.contains("d-none")); }

    launcher.addEventListener("click", toggle);
    launcher.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(); }
    });
    if (close) close.addEventListener("click", () => show(false));

    function addMsg(text, isUser) {
      const div = document.createElement("div");
      div.className = isUser ? "mb-user" : "mb-ai";
      div.textContent = text;
      msgs.appendChild(div);
      msgs.scrollTop = msgs.scrollHeight;
    }

    // ترحيب أولي
    addMsg("مرحباً! اسألني عن أي رقم في اللوحة: مبيعات اليوم، الذروات، الفروع، المرتجعات، أو خطة لرفع الهامش.", false);

    async function fire() {
      const msg = (inp.value || "").trim();
      if (!msg) return;
      addMsg(msg, true);
      inp.value = "";
      send.disabled = true;
      const loading = document.createElement("div");
      loading.className = "mb-ai";
      loading.textContent = "… يفكّر مانوس";
      msgs.appendChild(loading);
      msgs.scrollTop = msgs.scrollHeight;
      try {
        const r = await fetch(E.chatJson, {
          method: "POST",
          credentials: "same-origin",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({ message: msg }),
        });
        const j = await r.json().catch(() => ({}));
        loading.remove();
        if (!r.ok || !j.ok) addMsg(j.error || "تعذّر الرد.", false);
        else addMsg(j.reply || "—", false);
      } catch (_) {
        loading.remove();
        addMsg("خطأ شبكة.", false);
      } finally {
        send.disabled = false;
      }
    }

    send.addEventListener("click", fire);
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); fire(); }
    });
  }

  // ── Lazy renderer ──
  function whenVisible(el, fn) {
    if (!el) { fn(); return; }
    if (!("IntersectionObserver" in window)) { fn(); return; }
    const io = new IntersectionObserver(
      (entries) => entries.forEach((en) => {
        if (!en.isIntersecting) return;
        io.disconnect();
        fn();
      }),
      { rootMargin: "80px", threshold: 0.05 }
    );
    io.observe(el);
  }

  document.addEventListener("DOMContentLoaded", function () {
    // الرسم الأولي من SSR
    applyKpis(SSR);
    renderBranchChart((SSR && SSR.branches_breakdown) || []);

    // تأجيل الرؤى حتى ظهور القسم
    whenVisible(document.getElementById("mnInsightsBody"), loadInsights);

    // أزرار يدوية
    const refresh = document.getElementById("mnRefreshInsights");
    if (refresh) refresh.addEventListener("click", loadInsights);

    // تحديث المقاييس كل 60 ثانية
    setTimeout(loadMetricsDeferred, 800);
    setInterval(loadMetricsDeferred, 60000);

    setupChat();
  });
})();
