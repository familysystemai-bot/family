/**
 * قائمة جانبية منزلقة للجوال فقط (max-width: 768px)
 * الشريط: aside.dash-sidebar-col + class .open
 * التعتيم: .sidebar-overlay + class .active (z-index تحت الشريط)
 */
(function () {
  function init(root) {
    if (!root) return;
    var btn = root.querySelector(".dash-menu-btn");
    var overlay =
      root.querySelector(".sidebar-overlay") || root.querySelector(".dash-backdrop");
    var aside = root.querySelector("aside.dash-sidebar-col");
    var panel = root.querySelector(".dash-sidebar-panel");
    if (!btn || !aside) return;

    var mqMobileDrawer = window.matchMedia("(max-width: 768px)");

    function isMobileDrawer() {
      return mqMobileDrawer.matches;
    }

    function setOpen(open) {
      if (!isMobileDrawer()) {
        root.classList.remove("dash-nav-open");
        document.body.classList.remove("dash-nav-open");
        document.documentElement.classList.remove("dash-nav-open");
        document.body.style.overflow = "";
        document.documentElement.style.overflow = "";
        aside.classList.remove("open");
        if (overlay) overlay.classList.remove("active");
        btn.setAttribute("aria-expanded", "false");
        return;
      }
      root.classList.toggle("dash-nav-open", open);
      document.body.classList.toggle("dash-nav-open", open);
      document.documentElement.classList.toggle("dash-nav-open", open);
      aside.classList.toggle("open", open);
      if (overlay) overlay.classList.toggle("active", open);
      if (open) {
        document.body.style.overflow = "hidden";
        document.documentElement.style.overflow = "hidden";
      } else {
        document.body.style.overflow = "";
        document.documentElement.style.overflow = "";
      }
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    }

    function closeSidebar() {
      setOpen(false);
    }

    function toggle() {
      setOpen(!root.classList.contains("dash-nav-open"));
    }

    btn.addEventListener("click", function (e) {
      e.preventDefault();
      toggle();
    });

    if (overlay) {
      overlay.addEventListener("click", function () {
        closeSidebar();
      });
    }

    var navHost = panel || aside;
    navHost.querySelectorAll('a, [data-bs-toggle="offcanvas"]').forEach(function (link) {
      link.addEventListener("click", function () {
        if (isMobileDrawer()) closeSidebar();
      });
    });

    mqMobileDrawer.addEventListener("change", function () {
      setOpen(false);
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && root.classList.contains("dash-nav-open")) {
        closeSidebar();
      }
    });
  }

  document.querySelectorAll(".dash-layout").forEach(init);
})();
