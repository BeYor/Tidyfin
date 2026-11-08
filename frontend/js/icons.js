const P = {
  overview: '<rect x="3" y="3" width="8" height="9" rx="2"/><rect x="13" y="3" width="8" height="5" rx="2"/><rect x="13" y="12" width="8" height="9" rx="2"/><rect x="3" y="16" width="8" height="5" rx="2"/>',
  queue: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>',
  history: '<path d="M4 12a8 8 0 1 0 2.5-5.8"/><path d="M4 4v4.2h4.2"/><path d="M12 8v4l2.6 1.6"/>',
  language: '<path d="M4 5h16v11h-8.5L7 19.5V16H4z"/><path d="M8 9v4M8 9h1.6a1.3 1.3 0 0 1 0 2.6H8M13.5 9v4h2.2"/>',
  logs: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M7 9.5l3 2.5-3 2.5M12.5 15h4.5"/>',
  settings: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  play: '<path d="M8 5.5l10.5 6.5L8 18.5z"/>',
  shield: '<path d="M12 3l7 3v5.2c0 4.6-3 7.7-7 9.8-4-2.1-7-5.2-7-9.8V6z"/><path d="M9 12l2.2 2.2L15.5 10"/>',
  clockplus: '<circle cx="11" cy="13" r="7.5"/><path d="M11 9v4l2.4 1.4M19 3v5M16.5 5.5h5"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17.4v.1"/>',
  star: '<path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.9L12 17l-5.2 2.7 1-5.9L3.5 9.7l5.9-.8z"/>',
  bolt: '<path d="M13 3L5 13.5h6L10 21l8-10.5h-6z"/>',
  link: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  logout: '<path d="M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M15 8l4 4-4 4M19 12H9"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  film: '<rect x="4" y="3" width="16" height="18" rx="2.5"/><path d="M4 8h16M4 16h16M9 3v18M15 3v18"/>',
  poster: '<rect x="5" y="3" width="14" height="18" rx="2.5"/><path d="M5 15l4-4 4 4 2-2 4 4"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  sort: '<path d="M7 4v16M7 20l-3-3M7 20l3-3M17 20V4M17 4l-3 3M17 4l3 3"/>',
  library: '<rect x="3" y="4" width="5" height="16" rx="1.5"/><rect x="10" y="4" width="5" height="16" rx="1.5"/><path d="M17.5 5.5l3 .8-3.6 13.4-3-.8z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.1"/>',
  bell: '<path d="M6 17h12l-1.5-2V10a4.5 4.5 0 0 0-9 0v5zM10 20a2 2 0 0 0 4 0"/>',
  heart: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.5-5.8"/><path d="M20 4v4.5h-4.5"/>',
};

export function icon(name, size) {
  const span = document.createElement("span");
  span.className = "ico";
  span.setAttribute("aria-hidden", "true");
  span.innerHTML = `<svg viewBox="0 0 24 24">${P[name] || ""}</svg>`;
  if (size) span.style.fontSize = size + "px";
  return span;
}
