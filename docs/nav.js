// Shared sidebar + top-bar behavior for every docs page.
//
// The nav markup itself is defined once here (NAV_STRUCTURE) and injected
// into each page's <nav class="side" id="sideNav"></nav> placeholder --
// single source of truth instead of duplicating ~90 lines of <li> markup
// across 7 HTML files. Each page sets `window.DOCS_PAGE` (its own
// filename, e.g. "language.html") before this script runs so the injected
// links can point `href="otherpage.html#anchor"` cross-page while staying
// bare `href="#anchor"` for same-page sections, and so the current page's
// nav group can be marked active on load (not just via scroll).

const NAV_STRUCTURE = [
  { page: "index.html", label: "Overview", sections: [
    ["intro", "Introduction"], ["status", "Project status"],
    ["install", "Installation"], ["quickstart", "Quick start"],
    ["cli", "CLI reference"],
  ]},
  { page: "api.html", label: "Public API", sections: [
    ["api-overview", "Overview"], ["api-backend", "asmpython.backend"],
    ["api-linker", "asmpython.linker"],
  ]},
  { page: "assembly.html", label: "Assembly & FFI", sections: [
    ["inline-asm", "Inline assembly"], ["assembly-class", "Assembly builder class"],
    ["ffi", "Inline FFI"], ["import-binary", "Dynamic loading (import_binary)"],
    ["mlang", "asmpython.mlang"],
  ]},
  { page: "stdlib.html", label: "Built-ins", sections: [
    ["mlang-configs", "Built-in mlang configs"],
  ]},
  { page: "targets.html", label: "Targets", sections: [
    ["target-windows", "Windows (PE64)"], ["target-linux", "Linux (ELF64)"],
    ["target-freestanding", "Freestanding"], ["target-freestanding16", "16-bit boot"],
  ]},
  { page: "internals.html", label: "Compiler Internals", sections: [
    ["architecture", "Architecture & pipeline"], ["backends", "Backends: legacy & x86-64"],
  ]},
  { page: "reference.html", label: "Reference", sections: [
    ["builtins", "Non-standard builtins"], ["error-codes", "Error codes"], ["limitations", "Limitations"],
  ]},
];

function buildNavHTML(currentPage) {
  let html = "<ul>";
  for (const group of NAV_STRUCTURE) {
    const isCurrent = group.page === currentPage;
    html += `<li class="sep">${group.label}</li>`;
    for (const [id, label] of group.sections) {
      const href = isCurrent ? `#${id}` : `${group.page}#${id}`;
      html += `<li><a href="${href}"><span class="nav-dot"></span>${label}</a></li>`;
    }
  }
  html += "</ul>";
  return html;
}

document.addEventListener("DOMContentLoaded", () => {
  const currentPage = window.DOCS_PAGE || "index.html";
  const sideNav = document.getElementById("sideNav");
  if (sideNav) sideNav.innerHTML = buildNavHTML(currentPage);

  const navToggle = document.getElementById("navToggle");
  if (navToggle && sideNav) {
    navToggle.addEventListener("click", () => sideNav.classList.toggle("open"));
    document.addEventListener("click", (e) => {
      if (window.innerWidth <= 900 && sideNav.classList.contains("open")
          && !sideNav.contains(e.target) && e.target !== navToggle && !navToggle.contains(e.target)) {
        sideNav.classList.remove("open");
      }
    });
  }

  const navSearch = document.getElementById("navSearch");
  if (navSearch && sideNav) {
    navSearch.addEventListener("input", () => {
      const q = navSearch.value.trim().toLowerCase();
      const items = sideNav.querySelectorAll("li:not(.sep)");
      items.forEach(li => {
        const a = li.querySelector("a");
        const match = !q || a.textContent.toLowerCase().includes(q);
        a.classList.toggle("hidden", !match);
      });
      sideNav.querySelectorAll("li.sep").forEach(sep => {
        let n = sep.nextElementSibling, anyVisible = false;
        while (n && !n.classList.contains("sep")) {
          const a = n.querySelector("a");
          if (a && !a.classList.contains("hidden")) anyVisible = true;
          n = n.nextElementSibling;
        }
        sep.classList.toggle("hidden", !anyVisible);
      });
    });
  }

  // Highlight the active nav link on scroll, for sections that live on
  // THIS page (bare `#id` hrefs -- cross-page links never match a local
  // section id, so they're simply never marked active by this observer,
  // which is correct: a link to another page's anchor isn't "current"
  // while scrolling this page).
  const sections = document.querySelectorAll("main section[id]");
  const links = sideNav ? sideNav.querySelectorAll("li a") : [];
  if (sections.length && links.length) {
    const obs = new IntersectionObserver(entries => {
      entries.forEach(e => {
        if (e.isIntersecting) {
          links.forEach(l => l.classList.remove("active"));
          const a = sideNav.querySelector(`li a[href="#${e.target.id}"]`);
          if (a) a.classList.add("active");
        }
      });
    }, { rootMargin: "-20% 0px -70% 0px" });
    sections.forEach(s => obs.observe(s));
  }
});
