/**
 * Ask Yaffo walkthrough — docs/guide/start-here/ask-yaffo.md
 *
 * The docs sandbox has no provider key, so the assistant can't answer. Only the
 * assistant's JSON endpoints are simulated (conversation list, conversation
 * polls, sending, and plan approve/undo), with payloads shaped like the server's
 * schemas (site_agents/assistant/schemas.py). The real chat client, plan cards,
 * context chip and Settings section render them. Nothing reaches the fixture
 * database: no conversation or plan is created.
 */
import type {Page, Route} from "@playwright/test";
import {defineWalkthrough} from "../../_support";

const DOCS_URL = "https://jason-turan0.github.io/yaffo/guide";
const UPDATED = "2026-09-26T09:12:00+00:00";

// Keys the client reads on load (static/assistant/assistant.js).
const STORED_CONVERSATION = "yaffo.assistant.conversation";
const STORED_OPEN = "yaffo.assistant.open";

type Json = Record<string, unknown>;

interface Conversation {
    id: number;
    title: string;
    pending: number;
    messages: Json[];
}

const summary = (conversation: Conversation, status = "IDLE"): Json => ({
    id: conversation.id,
    title: conversation.title,
    status,
    updated_at: UPDATED,
    pending_plans: conversation.pending,
});

const tool = (payload: Json): Json => ({
    tool: "",
    sources: [],
    query: "",
    count: 0,
    title: "",
    error: false,
    args: {},
    detail: "",
    purpose: "",
    script: "",
    links: [],
    opens: [],
    plan_id: null,
    ...payload,
});

const messages = (...events: Array<[string, string, Json | null]>): Json[] =>
    events.map(([type, content, payload], index) => ({seq: index + 1, type, content, payload}));

// ---- the conversations the shots show ------------------------------------------

const COUNT_SCRIPT = [
    'maya = [p for p in data_query({"source": "people"}) if p["name"] == "Maya Bennett"][0]',
    'face_ids = [r["face_id"] for r in data_query({"source": "people_face", "person_id": {"eq": maya["id"]}})]',
    'photos = {f["media_item_id"]: True for f in data_query({"source": "faces", "id": {"in": face_ids}})}',
    'dirs = data_query({"source": "media_dirs"})',
    'trip = data_query({"source": "media_items", "media_dir_id": {"eq": dirs[0]["id"]},',
    '                   "relative_path": {"prefix": "2021_gulf_beach_trip/"}})',
    'len([r for r in trip if r["id"] in photos])',
].join("\n");

const HELP: Conversation = {
    id: 901,
    title: "Photo folders and Maya at the beach",
    pending: 0,
    messages: messages(
        ["user", "How do I add my photo folders?", null],
        ["tool", "", tool({
            tool: "search_docs",
            query: "add photo folders",
            count: 3,
            sources: [
                {title: "Getting Started", heading: "Choose Your Photo Folders",
                    url: `${DOCS_URL}/start-here/getting-started/#choose-your-photo-folders`, scope: "guide"},
                {title: "Settings Reference", heading: "Media Directories",
                    url: `${DOCS_URL}/reference-maintenance/settings/#media-directories`, scope: "guide"},
            ],
        })],
        ["assistant",
            "Open Settings and, under Media Directories, type the folder's path or click Browse… to "
            + "pick it, then click Add Directory. Yaffo reads your photos where they are; it doesn't "
            + "move them. Then go to Utilities → Index Photos to index them.", null],
        ["user", "How many photos of Maya are from the beach trip?", null],
        ["tool", "", tool({
            tool: "run_script",
            purpose: "Count Maya's photos from the 2021 beach trip",
            script: COUNT_SCRIPT,
            detail: "Value: 8",
        })],
        ["tool", "", tool({
            tool: "link_to_photos",
            args: {title: "Maya at the Gulf beach trip"},
            count: 8,
            links: [{title: "Maya at the Gulf beach trip", url: "/?view=grid&year=2021"}],
        })],
        ["assistant", "Maya is in 8 of the 14 photos from the 2021 Gulf beach trip.", null],
    ),
};

const PLAN_SCRIPT = [
    'dirs = data_query({"source": "media_dirs"})',
    'rows = data_query({"source": "media_items", "media_dir_id": {"eq": dirs[0]["id"]},',
    '                   "relative_path": {"prefix": "2021_gulf_beach_trip/"}})',
    'ids = [r["id"] for r in rows]',
    'album = create_album("Gulf Beach Trip 2021")',
    "add_to_album(album, ids)",
    'tag_media_items([{"media_item_id": i, "name": "Gulf Coast"} for i in ids])',
    "len(ids)",
].join("\n");

const planStep = (seq: number, name: string, summaryText: string, count: number, facts: Json, state: string): Json => ({
    seq, name, summary: summaryText, count, facts, risk: "low", reversible: true, state, error: null,
    starts_job: false, job_id: null, job_page: null,
});

const plan = (status: string): Json => {
    const state = {PENDING: "pending", EXECUTED: "done", UNDONE: "undone"}[status] ?? "pending";
    return {
        id: 31,
        status,
        risk: "low",
        count: 14,
        reversible: true,
        read_only: false,
        confirm: null,
        steps: [
            planStep(0, "create_album", "Create album 'Gulf Beach Trip 2021'", 1,
                {album: "Gulf Beach Trip 2021"}, state),
            planStep(1, "add_to_album", "Add 14 photo(s) to an album", 14,
                {album: "Gulf Beach Trip 2021", new_album: true}, state),
            planStep(2, "tag_media_items", "Tag 14 photo(s)", 14, {names: ["Gulf Coast"], more: 0}, state),
        ],
        error: null,
        created_at: "2026-09-26T09:40:00+00:00",
        expires_at: "2026-09-26T10:10:00+00:00",
        finished_at: status === "PENDING" ? null : "2026-09-26T09:41:00+00:00",
    };
};

const albumConversation = (planStatus: string): Conversation => ({
    id: 902,
    title: "Gulf beach trip album",
    pending: planStatus === "PENDING" ? 1 : 0,
    messages: messages(
        ["user", "Make an album of the beach trip photos and tag them Gulf Coast", null],
        ["tool", "", tool({
            tool: "run_script",
            purpose: "Album and tag for the 2021 beach trip",
            script: PLAN_SCRIPT,
            detail: "Recorded change plan #31 (3 steps, low risk)",
            plan_id: 31,
            plan: plan(planStatus),
        })],
        ["assistant",
            "I found the 14 photos from the 2021 Gulf beach trip. The card below creates the album, "
            + "adds them to it, and tags them Gulf Coast. Nothing changes until you approve it.", null],
    ),
});

const OLDER: Conversation = {id: 900, title: "Why didn't last night's sync run?", pending: 0, messages: []};

// ---- simulated endpoints --------------------------------------------------------

const json = (route: Route, body: Json, status = 200): Promise<void> =>
    route.fulfill({status, contentType: "application/json", body: JSON.stringify(body)});

const statusBody = (conversation: Conversation, status = "IDLE"): Json => ({
    conversation: summary(conversation, status),
    status,
    started_at: status === "RUNNING" ? UPDATED : null,
    messages: conversation.messages,
    queue: null,
});

/**
 * Serve the conversation list and each conversation's poll from `conversations`
 * (looked up on every request, so a flow can change them between polls).
 */
const serveConversations = async (page: Page, conversations: () => Conversation[]): Promise<void> => {
    await page.route("**/api/assistant/conversations", async (route) => {
        if (route.request().method() !== "GET") return route.fallback();
        await json(route, {conversations: conversations().map((c) => summary(c))});
    });
    await page.route(/\/api\/assistant\/conversations\/(\d+)$/, async (route) => {
        if (route.request().method() !== "GET") return route.fallback();
        const id = Number(route.request().url().match(/(\d+)$/)?.[1]);
        const conversation = conversations().find((c) => c.id === id);
        if (!conversation) return json(route, {error: "Not found", code: "not_found"}, 404);
        await json(route, statusBody(conversation));
    });
};

const waitForAppReady = async (page: Page): Promise<void> => {
    await page.evaluate(async () => {
        const app = (window as typeof window & {PHOTO_ORGANIZER?: {appReady?: Promise<unknown>}}).PHOTO_ORGANIZER;
        if (!app?.appReady) throw new Error("Yaffo did not expose appReady");
        await app.appReady;
    });
};

/** Reload with the panel stored open on `conversationId` (null: a new conversation). */
const openPanelOn = async (page: Page, conversationId: number | null): Promise<void> => {
    await page.evaluate(([conversationKey, openKey, id]) => {
        if (id === null) window.localStorage.removeItem(conversationKey as string);
        else window.localStorage.setItem(conversationKey as string, String(id));
        window.sessionStorage.setItem(openKey as string, "true");
    }, [STORED_CONVERSATION, STORED_OPEN, conversationId]);
    await page.reload({waitUntil: "domcontentloaded"});
    await waitForAppReady(page);
    await page.locator("#assistant-panel:not([hidden])").waitFor();
};

const closePanelState = async (page: Page): Promise<void> => {
    await page.evaluate(([conversationKey, openKey]) => {
        window.localStorage.removeItem(conversationKey as string);
        window.sessionStorage.removeItem(openKey as string);
    }, [STORED_CONVERSATION, STORED_OPEN]);
};

const answers = (page: Page) => page.locator("#assistant-panel .chat-message-assistant");

export default defineWalkthrough({
    page: "start-here/ask-yaffo",

    shots: {
        "ask-yaffo-conversation.webp": {
            viewport: {width: 1440, height: 1000},
            goto: "/assistant",
            clip: ".assistant-page",
            setup: async (page) => {
                await serveConversations(page, () => [albumConversation("EXECUTED"), HELP, OLDER]);
                await page.evaluate(([key, id]) => window.localStorage.setItem(key, id),
                    [STORED_CONVERSATION, String(HELP.id)]);
                await page.reload({waitUntil: "domcontentloaded"});
                await waitForAppReady(page);
                await answers(page).nth(1).waitFor();
                // Expand the library question's activity down to its script.
                const activity = page.locator("#assistant-panel details.assistant-activity").nth(1);
                await activity.locator("summary").first().click();
                await activity.locator("details.assistant-tool > summary").first().click();
                await page.mouse.move(0, 0);
            },
        },
        // Cropped to the panel. The page behind is blanked: it can't show the failure
        // the chip names, and the crop's framing margin would show a sliver of it.
        "ask-yaffo-context.webp": {
            viewport: {width: 1440, height: 1000},
            goto: "/utilities/index-photos",
            clip: "#assistant-panel",
            setup: async (page) => {
                await serveConversations(page, () => []);
                await openPanelOn(page, null);
                await page.addStyleTag({content: ".main-container { visibility: hidden; }"});
                await page.evaluate(() => {
                    const app = (window as typeof window & {
                        PHOTO_ORGANIZER?: {assistant?: {instance?: {
                            openWithContext: (context: Record<string, string>, message: string) => void;
                        }}};
                    }).PHOTO_ORGANIZER;
                    const assistant = app?.assistant?.instance;
                    if (!assistant) throw new Error("The assistant panel did not initialize");
                    assistant.openWithContext({
                        page: "Utilities → Index Photos",
                        error_code: "filesystem_scan_failed",
                        error: "Could not scan the filesystem.",
                    }, "What went wrong here, and how do I fix it?");
                });
                await page.locator("#assistant-context:not([hidden])").waitFor();
                await page.mouse.move(0, 0);
            },
        },
        // A whole viewport: the panel floats over the page it was opened from.
        "ask-yaffo-change-card.webp": {
            viewport: {width: 1200, height: 860},
            goto: "/",
            setup: async (page) => {
                await serveConversations(page, () => [albumConversation("PENDING"), HELP]);
                await openPanelOn(page, 902);
                await page.locator("#assistant-panel .assistant-plan").waitFor();
                await page.mouse.move(0, 0);
            },
        },
        "settings-assistant.webp": {
            viewport: {width: 1400, height: 2600},
            goto: "/settings",
            clip: "#assistant-section",
            setup: async (page) => {
                const section = page.locator("#assistant-section");
                await section.locator("[data-assistant-action-group]").first().waitFor();
                await section.scrollIntoViewIfNeeded();
            },
        },
    },

    flows: async ({page, visit}) => {
        try {
            // The full page lists conversations and opens one from the list.
            let album = albumConversation("PENDING");
            const help: Conversation = {...HELP, messages: [...HELP.messages]};
            await serveConversations(page, () => [album, help, OLDER]);
            await visit("/assistant");
            await waitForAppReady(page);
            const list = page.locator("#assistant-conversation-list .assistant-conversation-item");
            await list.nth(2).waitFor();
            await list.filter({hasText: HELP.title}).click();
            await answers(page).nth(1).filter({hasText: "8 of the 14"}).waitFor();
            await page.locator("#assistant-panel .assistant-sources a")
                .filter({hasText: "Choose Your Photo Folders"}).waitFor();
            await page.locator("#assistant-panel .assistant-links a").filter({hasText: "Maya"}).waitFor();

            // Sending a follow-up: the reply is written while the client polls.
            const followUp = "Which of those is Maya's favorite?";
            let polls = 0;
            await page.route(`**/api/assistant/conversations/${HELP.id}/messages`, async (route) => {
                help.messages = [...help.messages, {seq: help.messages.length + 1, type: "user", content: followUp,
                    payload: null}];
                await json(route, {conversation: summary(help, "RUNNING")}, 202);
            });
            await page.route(`**/api/assistant/conversations/${HELP.id}`, async (route) => {
                if (route.request().method() !== "GET") return route.fallback();
                polls += 1;
                if (polls === 1) return json(route, statusBody(help, "RUNNING"));
                if (!help.messages.some((m) => m.type === "assistant" && String(m.content).includes("favorite"))) {
                    help.messages = [...help.messages, {seq: help.messages.length + 1, type: "assistant",
                        content: "None of them is marked as a favorite yet.", payload: null}];
                }
                await json(route, statusBody(help));
            });
            await page.locator("#assistant-chat-message").fill(followUp);
            await page.locator('#assistant-chat-form button[type="submit"]').click();
            await answers(page).filter({hasText: "marked as a favorite"}).waitFor({timeout: 15_000});
            await page.unroute(`**/api/assistant/conversations/${HELP.id}/messages`);
            await page.unroute(`**/api/assistant/conversations/${HELP.id}`);

            // Approve the proposed change, then undo it through the confirm dialog.
            await page.route("**/api/assistant/conversations/902/plans/31/approve", async (route) => {
                album = albumConversation("EXECUTED");
                await json(route, {plan: plan("EXECUTED")});
            });
            await page.route("**/api/assistant/conversations/902/plans/31/undo", async (route) => {
                album = albumConversation("UNDONE");
                await json(route, {plan: plan("UNDONE")});
            });
            await list.filter({hasText: "Gulf beach trip album"}).click();
            const card = page.locator("#assistant-panel .assistant-plan");
            await card.getByRole("button", {name: "Approve"}).click();
            await card.getByRole("button", {name: "Undo"}).waitFor();
            await card.getByRole("button", {name: "Undo"}).click();
            await page.locator("#global-confirm-dialog.active").waitFor();
            await page.locator("#confirm-dialog-confirm").click();
            await page.locator("#assistant-panel .assistant-plan").filter({hasText: "Undone."}).waitFor();

            // A contextual question opens the panel with its context attached.
            await visit("/utilities/index-photos");
            await waitForAppReady(page);
            await page.evaluate(() => {
                const app = (window as typeof window & {
                    PHOTO_ORGANIZER?: {assistant?: {instance?: {
                        openWithContext: (context: Record<string, string>, message: string) => void;
                    }}};
                }).PHOTO_ORGANIZER;
                app?.assistant?.instance?.openWithContext(
                    {page: "Utilities → Index Photos", error_code: "filesystem_scan_failed"},
                    "What went wrong here, and how do I fix it?");
            });
            await page.locator("#assistant-context:not([hidden])").filter({hasText: "filesystem_scan_failed"})
                .waitFor();
            await page.locator("#assistant-context-remove").click();
            await page.locator("#assistant-context").waitFor({state: "hidden"});

            // The Settings section: every diagnostics group and change group is listed.
            await visit("/settings");
            const section = page.locator("#assistant-section");
            await section.locator("#assistant-diag-metadata").waitFor();
            if (await section.locator("[data-assistant-action-group]").count() !== 8) {
                throw new Error("Settings → Assistant should list eight groups of changes");
            }
        } finally {
            await page.unrouteAll({behavior: "ignoreErrors"}).catch(() => undefined);
            await closePanelState(page).catch(() => undefined);
        }
    },
});
