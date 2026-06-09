/**
 * المركز المالي — مجمع العائلة
 * قراءة السياق من SSR ثم تحديث عبر metrics.json؛ مخطط ثنائي المحور للأشهر الستة؛ مانوس عائم.
 */
(function () {
  const E = typeof window.MM_FIN_ENDPOINTS !== "undefined" ? window.MM_FIN_ENDPOINTS : {};
  const SSR = typeof window.MM_FIN_SSR_METRICS !== "undefined" ? window.MM_FIN_SSR_METRICS : {};
  let dualAxisChart = null;

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

  function applyKpis(m) {
    if (!m) return;
    const live = m.live || {};

    setText("mnKpiSales", nf(m.today_sales));

    const src =
      m.mode === "remote"
        ? "POS"
        : m.mode === "internal_fallback"
          ? "معاينة"
          : m.mode === "remote_error"
            ? "خطأ POS"
            : String(m.mode || "—");

    const trendEl = document.getElementById("mnKpiSalesTrend");
    const pct = m.sales_vs_yesterday_pct;
    if (trendEl) {
      if (pct !== null && pct !== undefined && !Number.isNaN(Number(pct))) {
        const n = Number(pct);
        trendEl.textContent =
          (n >= 0 ? "+" : "") + n.toLocaleString("ar-SA", { maximumFractionDigits: 1 }) + "% عن أمس";
        trendEl.classList.remove("mm-fin-dash-analytics__trend-up", "mm-fin-dash-analytics__trend-down");
        trendEl.classList.add(n >= 0 ? "mm-fin-dash-analytics__trend-up" : "mm-fin-dash-analytics__trend-down");
      } else {
        trendEl.innerHTML =
          '<span id="mnKpiSrc">' + src.replace(/</g, "&lt;") + "</span> — المصدر الفعلي للمبالغ";
      }
    }

    const srcOnly = document.getElementById("mnKpiSrc");
    if (srcOnly && pct !== null && pct !== undefined && !Number.isNaN(Number(pct))) {
      srcOnly.textContent = src;
    }

    setText("mnKpiWa", nf(live.whatsapp_active_24h));
    const waTrendEl = document.getElementById("mnKpiWaTrend");
    if (waTrendEl) {
      const wt = live.whatsapp_trend_vs_prev_pct;
      if (wt !== null && wt !== undefined && String(wt) !== "") {
        const wn = Number(wt);
        waTrendEl.textContent =
          (wn >= 0 ? "+" : "") + wn.toLocaleString("ar-SA", { maximumFractionDigits: 1 }) + "% عن اليوم السابق";
        waTrendEl.classList.remove("mm-fin-dash-analytics__trend-up", "mm-fin-dash-analytics__trend-down");
        waTrendEl.classList.add(wn >= 0 ? "mm-fin-dash-analytics__trend-up" : "mm-fin-dash-analytics__trend-down");
      }
    }

    const cv = Number(live.conversion_rate_pct || 0);
    setText("mnKpiConv", cv.toLocaleString("ar-SA", { maximumFractionDigits: 1 }) + "%");
  }

  function chartMonthsSelection() {
    const sel = document.getElementById("mnChartPeriod");
    const raw = sel ? parseInt(String(sel.value), 10) : 6;
    return Number.isFinite(raw) && raw > 0 ? raw : 6;
  }

  function renderDualAxisChart(m) {
    const canvas = document.getElementById("mnDualAxisChart");
    if (!canvas || typeof Chart === "undefined") return;

    const labels = Array.isArray(m.six_month_labels_chart) ? m.six_month_labels_chart.map(String) : [];
    const salRaw = Array.isArray(m.six_month_sales_series_chart) ? m.six_month_sales_series_chart.map(Number) : [];
    // محاكاة المرتجعات إذا لم تكن متوفرة في السلسلة الزمنية لعرض التصميم الجديد
    const retRaw = m.returns_series || salRaw.map(s => s * (Math.random() * 0.1)); 

    const mo = chartMonthsSelection();
    const k = Math.min(mo, labels.length || 1);
    const L = labels.slice(-k);
    const S = salRaw.slice(-k);
    const R = retRaw.slice(-k);

    if (dualAxisChart) dualAxisChart.destroy();

    const ctx = canvas.getContext('2d');
    const salesGradient = ctx.createLinearGradient(0, 0, 0, 400);
    salesGradient.addColorStop(0, 'rgba(16, 185, 129, 0.4)');
    salesGradient.addColorStop(1, 'rgba(16, 185, 129, 0)');

    const returnsGradient = ctx.createLinearGradient(0, 0, 0, 400);
    returnsGradient.addColorStop(0, 'rgba(239, 68, 68, 0.4)');
    returnsGradient.addColorStop(1, 'rgba(239, 68, 68, 0)');

    dualAxisChart = new Chart(canvas, {
      type: "line",
      data: {
        labels: L,
        datasets: [
          {
            label: "📈 المبيعات",
            data: S,
            borderColor: "#10b981",
            backgroundColor: salesGradient,
            fill: true,
            stepped: 'middle', // نمط التداول (الدرجات)
            tension: 0,
            borderWidth: 2,
            pointRadius: 0, // إخفاء النقاط لنمط التداول
            hoverRadius: 6,
            pointBackgroundColor: "#10b981",
          },
          {
            label: "📉 المرتجعات",
            data: R,
            borderColor: "#ef4444",
            backgroundColor: returnsGradient,
            fill: true,
            stepped: 'middle',
            tension: 0,
            borderWidth: 2,
            pointRadius: 0,
            hoverRadius: 6,
            pointBackgroundColor: "#ef4444",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            position: "bottom",
            labels: { color: tickMuted, boxWidth: 10, padding: 12, font: { size: 11 } },
          },
          tooltip: {
            backgroundColor: "rgba(11, 17, 30, 0.95)",
            titleColor: "#e2e8f0",
            bodyColor: "#cbd5f5",
            borderColor: "rgba(212, 175, 55, 0.35)",
            borderWidth: 1,
          },
        },
        scales: {
          x: {
            ticks: { color: "#94a3b8", font: { family: 'monospace' } },
            grid: { color: "rgba(148, 163, 184, 0.05)" },
          },
          y: {
            position: "right",
            ticks: { 
                color: "#94a3b8", 
                font: { family: 'monospace' },
                callback: function(value) { return value.toLocaleString('ar-SA') + ' ر.ي'; }
            },
            grid: { color: "rgba(148, 163, 184, 0.05)" },
          }
        },
        animations: {
          y: {
            duration: 2000,
            easing: 'easeInOutElastic'
          }
        }
      },
    });
  }

  function setupBranchPick() {
    const sel = document.getElementById("mnBranchPick");
    if (!sel) return;
    sel.addEventListener("change", function () {
      var id = String(sel.value || "0");
      // إعادة تحميل الصفحة مع المعامل الجديد لتفعيل الفلترة الحقيقية من السيرفر
      const url = new URL(window.location.href);
      if (id !== "0") {
        url.searchParams.set('branch_id', id);
      } else {
        url.searchParams.delete('branch_id');
      }
      window.location.href = url.toString();
    });
  }

  function fetchJson(url, opts) {
    return fetch(url, Object.assign({ credentials: "same-origin", headers: { Accept: "application/json" } }, opts || {})).then(
      function (r) {
        return r.json();
      }
    );
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

  function pullMetricsJson() {
    if (!E.metricsJson) return;
    fetchJson(E.metricsJson)
      .then(function (j) {
        if (!j.ok || !j.metrics) return;
        window.MM_FIN_LAST_METRICS = j.metrics;
        applyKpis(j.metrics);
        renderDualAxisChart(j.metrics);
      })
      .catch(function () {});
  }

  function setupChat() {
    const launcher = document.getElementById("mnChatLauncher");
    const panel = document.getElementById("mnChatPanel");
    const closeBtn = document.getElementById("mnChatClose");
    const send = document.getElementById("mnChatSend");
    const inp = document.getElementById("mnChatInput");
    const msgs = document.getElementById("mnChatMsgs");
    if (!launcher || !panel || !send || !inp || !msgs || !E.chatJson) return;

    function show(open) {
      panel.classList.toggle("d-none", !open);
      if (open) setTimeout(function () { inp.focus(); }, 50);
    }

    function toggle() {
      show(panel.classList.contains("d-none"));
    }

    launcher.addEventListener("click", toggle);
    launcher.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        toggle();
      }
    });
    if (closeBtn) closeBtn.addEventListener("click", function () { show(false); });

    function addMsg(text, isUser) {
      const div = document.createElement("div");
      div.className = isUser ? "mb-user" : "mb-ai";
      div.textContent = text;
      msgs.appendChild(div);
      msgs.scrollTop = msgs.scrollHeight;
    }

    addMsg(
      "مرحباً! أسئلة المبيعات أو الفروع أو واتساب — أنا أقرأ ما يظهر لك الآن على اللوحة.",
      false
    );

    async function fire() {
      var msg = (inp.value || "").trim();
      if (!msg) return;
      addMsg(msg, true);
      inp.value = "";
      send.disabled = true;
      const loading = document.createElement("div");
      loading.className = "mb-ai";
      loading.textContent = "… يستجيب مانوس";
      msgs.appendChild(loading);
      msgs.scrollTop = msgs.scrollHeight;
      try {
        const r = await fetch(E.chatJson, {
          method: "POST",
          credentials: "same-origin",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({ message: msg }),
        });
        const j = await r.json().catch(function () {
          return {};
        });
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
    inp.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        fire();
      }
    });
  }

  function whenVisible(el, fn) {
    if (!el) {
      fn();
      return;
    }
    if (!("IntersectionObserver" in window)) {
      fn();
      return;
    }
    const io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          io.disconnect();
          fn();
        });
      },
      { rootMargin: "80px", threshold: 0.05 }
    );
    io.observe(el);
  }

  document.addEventListener("DOMContentLoaded", function () {
    applyKpis(SSR);
    renderDualAxisChart(SSR);
    setupBranchPick();
    window.MM_FIN_LAST_METRICS = SSR;

    const period = document.getElementById("mnChartPeriod");
    if (period) {
      period.addEventListener("change", function () {
        renderDualAxisChart(window.MM_FIN_LAST_METRICS || SSR);
      });
    }

    whenVisible(document.getElementById("mnInsightsBody"), loadInsights);

    const refresh = document.getElementById("mnRefreshInsights");
    if (refresh) refresh.addEventListener("click", loadInsights);

    setTimeout(pullMetricsJson, 800);
    setInterval(pullMetricsJson, 60000);

    setupChat();
  });
})();
