const host = window.location.hostname;
const port = window.location.port ? `:${window.location.port}` : "";
const protocol = window.location.protocol;

const deviceHosts = {
    a: host.replace(/^demo(?=\.)/, "demo-a"),
    b: host.replace(/^demo(?=\.)/, "demo-b"),
};

document.querySelectorAll("[data-device-link]").forEach((link) => {
    const device = link.dataset.deviceLink;
    const path = link.dataset.devicePath || "/";
    link.href = `${protocol}//${deviceHosts[device]}${port}${path}`;
});

const RESET_HOUR = 7;
const RESET_MINUTE = 45;
const now = new Date();
const chicagoParts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Chicago",
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    second: "numeric",
    hourCycle: "h23",
}).formatToParts(now);
const part = (type) => Number(chicagoParts.find((entry) => entry.type === type).value);
const currentMinutes = part("hour") * 60 + part("minute");
const dayOffset = currentMinutes >= RESET_HOUR * 60 + RESET_MINUTE ? 1 : 0;
const resetLabel = `${dayOffset ? "tomorrow" : "today"} at 7:45 AM CT`;

document.querySelector("[data-next-reset]").textContent = resetLabel;
