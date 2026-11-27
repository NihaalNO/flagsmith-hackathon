// Legacy script: the client comes from a global, so it cannot be traced to an import.
export function trackCheckout(event) {
  if (window.flagsmith.hasFeature("analytics_v2")) {
    sendBeacon("/v2/events", event);
  }
}

function sendBeacon(url, body) {
  return navigator.sendBeacon(url, JSON.stringify(body));
}
