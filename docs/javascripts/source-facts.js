(() => {
  const loadSourceFacts = async () => {
    const source = document.querySelector("[data-dj-hyperview-source]");
    if (!source || source.dataset.factsLoaded === "true") {
      return;
    }

    const parts = new URL(source.href).pathname.split("/").filter(Boolean);
    if (parts.length !== 2) {
      return;
    }

    source.dataset.factsLoaded = "true";
    const repository = source.querySelector(".md-source__repository");
    if (!repository) {
      return;
    }

    const api = `https://api.github.com/repos/${parts[0]}/${parts[1]}`;
    const fetchJson = async (url, fallback) => {
      try {
        const response = await fetch(url);
        return response.ok ? await response.json() : fallback;
      } catch {
        return fallback;
      }
    };
    const [releases, metadata] = await Promise.all([
      fetchJson(`${api}/releases?per_page=1`, []),
      fetchJson(api, {}),
    ]);

    const release = Array.isArray(releases) ? releases[0] : undefined;
    const format = new Intl.NumberFormat("en", {
      notation: "compact",
      compactDisplay: "short",
    });
    const facts = [];
    if (release && typeof release.tag_name === "string") {
      facts.push(["version", release.tag_name]);
    }
    if (Number.isFinite(metadata.stargazers_count)) {
      facts.push(["stars", format.format(metadata.stargazers_count)]);
    }
    if (Number.isFinite(metadata.forks_count)) {
      facts.push(["forks", format.format(metadata.forks_count)]);
    }
    if (facts.length === 0) {
      return;
    }

    const list = document.createElement("ul");
    list.className = "md-source__facts";
    for (const [name, value] of facts) {
      const item = document.createElement("li");
      item.className = `md-source__fact md-source__fact--${name}`;
      item.textContent = value;
      list.appendChild(item);
    }
    repository.appendChild(list);
    repository.classList.add("md-source__repository--active");
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", loadSourceFacts, { once: true });
  } else {
    loadSourceFacts();
  }
})();
