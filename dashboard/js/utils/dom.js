export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);

  Object.entries(attrs).forEach(([key, value]) => {
    if (value === null || value === undefined) return;
    if (key === 'className') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else {
      node.setAttribute(key, value);
    }
  });

  const childList = Array.isArray(children) ? children : [children];
  childList.forEach((child) => {
    if (child === null || child === undefined) return;
    node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  });

  return node;
}

export function setChildren(parent, children) {
  parent.replaceChildren(...children);
}

export function badge(text, variant = 'muted') {
  return el('span', { className: `badge badge-${variant}`, text });
}

export function statusDot(variant = 'gray') {
  return el('span', { className: `dot dot-${variant}` });
}
