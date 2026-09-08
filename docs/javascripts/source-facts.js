(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.djHyperviewSourceFacts = api;
    api.install(root);
  }
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";

  function newestPublishedRelease(releases) {
    if (!Array.isArray(releases)) {
      return undefined;
    }
    let newest;
    let newestTimestamp = -Infinity;
    for (const release of releases) {
      if (!release || release.draft === true || typeof release.tag_name !== "string") {
        continue;
      }
      const timestamp = Date.parse(release.published_at);
      if (Number.isFinite(timestamp) && timestamp > newestTimestamp) {
        newest = release;
        newestTimestamp = timestamp;
      }
    }
    return newest;
  }

  async function loadSourceFacts(root) {
    const document = root.document;
    const source = document.querySelector("[data-dj-hyperview-source]");
    if (!source || source.dataset.factsLoaded === "true") {
      return;
    }

    let parts;
    try {
      parts = new root.URL(source.href).pathname.split("/").filter(Boolean);
    } catch (_error) {
      return;
    }
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
        const response = await root.fetch(url);
        return response.ok ? await response.json() : fallback;
      } catch (_error) {
        return fallback;
      }
    };
    const [releases, metadata] = await Promise.all([
      fetchJson(`${api}/releases?per_page=100`, []),
      fetchJson(api, {}),
    ]);

    const release = newestPublishedRelease(releases);
    const format = new root.Intl.NumberFormat("en", {
      notation: "compact",
      compactDisplay: "short",
    });
    const facts = [];
    if (release) {
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
  }

  function install(root) {
    const document = root.document;
    if (!document) {
      return;
    }
    if (document.readyState === "loading") {
      document.addEventListener(
        "DOMContentLoaded",
        function () { loadSourceFacts(root); },
        {once: true},
      );
    } else {
      loadSourceFacts(root);
    }
  }

  return {
    install: install,
    loadSourceFacts: loadSourceFacts,
    newestPublishedRelease: newestPublishedRelease,
  };
});
