(() => {
  // Mobile navigation
  const toggle = document.querySelector(".menu-toggle");
  const nav = document.getElementById("site-nav");
  if (toggle && nav) {
    const setOpen = (open) => {
      nav.classList.toggle("is-open", open);
      toggle.setAttribute("aria-expanded", String(open));
    };
    toggle.addEventListener("click", () => setOpen(!nav.classList.contains("is-open")));
    nav.querySelectorAll("a").forEach((a) => a.addEventListener("click", () => setOpen(false)));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") setOpen(false);
    });
  }

  // Footer year
  document.querySelectorAll("[data-year]").forEach((el) => {
    el.textContent = String(new Date().getFullYear());
  });

  // Contact form: builds a mailto: link from the fields (no data leaves the browser until the user sends the email)
  const form = document.getElementById("contact-form");
  const link = document.getElementById("mailto-link");
  if (!form || !link) return;

  const t = form.dataset;
  const value = (id) => document.getElementById(id).value.trim();

  const update = () => {
    const name = value("name");
    const message = value("message");
    if (name.length > 1 && message.length > 1) {
      const company = value("company");
      const type = value("projectType");
      const budget = value("budget");
      let body = `${t.greeting}\n\n${message}\n\n---\n${t.labelName}: ${name}\n`;
      if (company) body += `${t.labelCompany}: ${company}\n`;
      if (type) body += `${t.labelType}: ${type}\n`;
      if (budget) body += `${t.labelBudget}: ${budget}\n`;
      const subject = `${t.subject} ${name}`;
      link.href = `mailto:info@colomba.link?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
      link.classList.remove("is-disabled");
      link.removeAttribute("aria-disabled");
    } else {
      link.href = "#";
      link.classList.add("is-disabled");
      link.setAttribute("aria-disabled", "true");
    }
  };

  form.querySelectorAll("input, select, textarea").forEach((el) => {
    el.addEventListener("input", update);
    el.addEventListener("change", update);
  });
  link.addEventListener("click", (e) => {
    if (link.classList.contains("is-disabled")) {
      e.preventDefault();
      form.reportValidity();
    }
  });
  update();
})();
