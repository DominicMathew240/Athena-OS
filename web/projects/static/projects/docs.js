/* Docs wiki: live search, audience filter, table-of-contents highlighting, and CLI command copy. */
(() => {
  "use strict";
  const { toast } = window.AthenaApp;

  const searchInput = document.getElementById("wiki-search");
  const clearBtn = document.getElementById("wiki-clear");
  const countNode = document.getElementById("wiki-count");
  const emptyNode = document.getElementById("wiki-empty");
  const audienceGroup = document.getElementById("wiki-audience");
  const sections = [...document.querySelectorAll(".wiki-section")];
  const tocLinks = [...document.querySelectorAll("[data-toc]")];

  let activeAudience = "all";

  function applyFilter() {
    const query = (searchInput.value || "").trim().toLowerCase();
    clearBtn.hidden = !query;
    let visible = 0;

    sections.forEach(section => {
      const audiences = (section.dataset.audience || "").split(/\s+/);
      const matchesAudience = activeAudience === "all" || audiences.includes(activeAudience);
      const hay = `${section.textContent} ${section.dataset.keywords || ""}`.toLowerCase();
      const matchesQuery = !query || hay.includes(query);
      const show = matchesAudience && matchesQuery;
      section.hidden = !show;
      if (show) visible += 1;

      const tocLink = tocLinks.find(link => link.dataset.toc === section.id);
      if (tocLink) tocLink.parentElement.hidden = !show;
    });

    emptyNode.hidden = visible > 0;
    countNode.textContent =
      visible === sections.length
        ? `Showing all ${sections.length} guides`
        : `Showing ${visible} of ${sections.length} guides`;
  }

  searchInput.addEventListener("input", applyFilter);
  clearBtn.addEventListener("click", () => {
    searchInput.value = "";
    applyFilter();
    searchInput.focus();
  });

  audienceGroup.addEventListener("click", event => {
    const btn = event.target.closest("[data-audience]");
    if (!btn) return;
    activeAudience = btn.dataset.audience;
    audienceGroup.querySelectorAll("[data-audience]").forEach(node => {
      node.setAttribute("aria-pressed", node === btn ? "true" : "false");
    });
    applyFilter();
  });

  function setCurrentToc(id) {
    tocLinks.forEach(link => {
      if (link.dataset.toc === id) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
  }

  if ("IntersectionObserver" in window && sections.length) {
    const observer = new IntersectionObserver(entries => {
      const visible = entries.filter(entry => entry.isIntersecting);
      if (visible.length) setCurrentToc(visible[0].target.id);
    }, { rootMargin: "-15% 0px -70% 0px" });
    sections.forEach(section => observer.observe(section));
  }

  document.getElementById("wiki-toc").addEventListener("click", event => {
    const link = event.target.closest("[data-toc]");
    if (link) setCurrentToc(link.dataset.toc);
  });

  document.getElementById("wiki-body").addEventListener("click", async event => {
    const btn = event.target.closest("[data-copy]");
    if (!btn) return;
    const text = btn.dataset.copy || "";
    try {
      await navigator.clipboard.writeText(text);
      toast("Command copied to clipboard");
    } catch (error) {
      toast("Select the command text to copy it", "bad");
    }
  });

  applyFilter();
  if (location.hash) setCurrentToc(location.hash.slice(1));
  else if (sections[0]) setCurrentToc(sections[0].id);
})();
