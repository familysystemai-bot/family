/**
 * قائمة جانبية منزلقة للجوال فقط (max-width: 768px)
 */
(function () {
  function init(root) {
    if (!root) return;
    var btn = root.querySelector(".dash-menu-btn");
    var backdrop = root.querySelector(".dash-backdrop");
    var sidebar = root.querySelector(".dash-sidebar-panel");
    if (!btn || !sidebar) return;

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
        btn.setAttribute("aria-expanded", "false");
        return;
      }
      root.classList.toggle("dash-nav-open", open);
      document.body.classList.toggle("dash-nav-open", open);
      document.documentElement.classList.toggle("dash-nav-open", open);
      if (open) {
        document.body.style.overflow = "hidden";
        document.documentElement.style.overflow = "hidden";
      } else {
        document.body.style.overflow = "";
        document.documentElement.style.overflow = "";
      }
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    }

    function toggle() {
      setOpen(!root.classList.contains("dash-nav-open"));
    }

    btn.addEventListener("click", function (e) {
      e.preventDefault();
      toggle();
    });

    if (backdrop) {
      backdrop.addEventListener("click", function () {
        setOpen(false);
      });
    }

    sidebar.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        if (isMobileDrawer()) setOpen(false);
      });
    });

    mqMobileDrawer.addEventListener("change", function () {
      setOpen(false);
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && root.classList.contains("dash-nav-open")) {
        setOpen(false);
      }
    });
  }

  document.querySelectorAll(".dash-layout").forEach(init);
})();
