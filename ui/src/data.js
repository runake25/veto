const DATA = {
  org: "Bracken & Field",
  period: "October 2026",
  approver: "Priya Raman",
  charters: [
    { id: "office",   name: "Office supplies", cap: 800,  perOrder: 300, perUnit: 15, threshold: 50, approver: "Priya Raman",
      vendors: ["Kettle Supply", "Meridian Paper", "Northgate Tools"] },
    { id: "lunches",  name: "Client lunches",  cap: 600,  perOrder: 250, perUnit: 0,  threshold: 100, approver: "Tom Iverson",
      vendors: ["Brightline Foods"] },
    { id: "courier",  name: "Courier & post",  cap: 250,  perOrder: 120, perUnit: 0,  threshold: 50, approver: "Priya Raman",
      vendors: ["Swift Courier"] },
    { id: "software", name: "Software & tools", cap: 1200, perOrder: 600, perUnit: 0,  threshold: 250, approver: "Tom Iverson",
      vendors: ["Northwind Analytics", "Ferrite CI", "Papertrail"] }
  ],
  requests: [
    {
      id: "REQ-1044", agent: "desk", tag: "equipment", title: "Split ergonomic keyboard", charter: "office", vendor: "Northgate Tools",
      requester: "Mira Sandoval", channel: "#office-ops", status: "awaiting", at: 902,
      lines: [{ item: "Split ergonomic keyboard", qty: 1, unit: 128.0, vendor: "Northgate Tools" }],
      reasons: [{ rule: "approval_threshold", observed: 128.0, limit: 50.0 }]
    },
    {
      id: "REQ-1043", agent: "tools", tag: "software", title: "Northwind Analytics, annual", charter: "software", vendor: "Northwind Analytics",
      requester: "Tom Iverson", channel: "#finance", status: "captured", at: 898,
      lines: [{ item: "Northwind Analytics workspace, 12 seats, annual", qty: 1, unit: 480.0, vendor: "Northwind Analytics" }],
      reasons: [], order: "5O190127TN364715T", auth: "0AW21788M41920835", capture: "3C6794331A1184705"
    },
    {
      id: "REQ-1042", agent: "pantry", tag: "pantry", title: "Coffee capsules and toilet paper", charter: "office", vendor: "Kettle Supply",
      requester: "Dan Okafor", channel: "#office-ops", status: "awaiting", at: 869,
      lines: [
        { item: "Vertuo Intenso capsules, 30 ct", qty: 2, unit: 18.0, vendor: "Kettle Supply" },
        { item: "Bamboo toilet paper, 24 rolls", qty: 1, unit: 14.5, vendor: "Kettle Supply" }
      ],
      reasons: [
        { rule: "per_unit_cap", observed: 18.0, limit: 15.0 },
        { rule: "approval_threshold", observed: 50.5, limit: 50.0 }
      ],
      amendment: { by: "Priya Raman", at: "14:34", text: "only 2 boxes", from: 68.5, to: 50.5 }
    },
    {
      id: "REQ-1041", agent: "ship", tag: "shipping", title: "Courier run, sample boxes", charter: "courier", vendor: "Swift Courier",
      requester: "Oli Bennett", channel: "#studio", status: "awaiting", at: 820,
      lines: [{ item: "Same-day courier, 6 kg, 4 stops", qty: 1, unit: 64.0, vendor: "Swift Courier" }],
      reasons: [{ rule: "approval_threshold", observed: 64.0, limit: 50.0 }]
    },
    {
      id: "REQ-1039", agent: "lunch", tag: "hospitality", title: "Studio lunch, client visit", charter: "lunches", vendor: "Brightline Foods",
      requester: "Tom Iverson", channel: "#finance", status: "captured", at: 735,
      lines: [{ item: "Lunch for eight, Braithwaite & Co. visit", qty: 1, unit: 118.0, vendor: "Brightline Foods" }],
      reasons: [], order: "1KF48219YP9031420", auth: "7RB55102CD8836417", capture: "8JX22074MC4810933"
    },
    {
      id: "REQ-1037", agent: "tools", tag: "software", title: "Ferrite CI, monthly", charter: "software", vendor: "Ferrite CI",
      requester: "Mira Sandoval", channel: "#engineering", status: "auto", at: 662,
      lines: [{ item: "Ferrite CI build minutes, monthly", qty: 1, unit: 29.0, vendor: "Ferrite CI" }],
      reasons: []
    },
    {
      id: "REQ-1036", agent: "desk", tag: "stationery", title: "A4 copy paper, 5 reams", charter: "office", vendor: "Meridian Paper",
      requester: "Dan Okafor", channel: "#office-ops", status: "auto", at: 648,
      lines: [{ item: "A4 copy paper, 80 gsm, 5 reams", qty: 5, unit: 5.68, vendor: "Meridian Paper" }],
      reasons: []
    },
    {
      id: "REQ-1033", agent: "ship", tag: "shipping", title: "Return label batch", charter: "courier", vendor: "Swift Courier",
      requester: "Oli Bennett", channel: "#studio", status: "auto", at: 612,
      lines: [{ item: "Prepaid return labels, 10 pack", qty: 1, unit: 22.5, vendor: "Swift Courier" }],
      reasons: []
    },
    {
      id: "REQ-1031", agent: "lunch", tag: "hospitality", title: "Client dinner, 6 covers", charter: "lunches", vendor: "The Ivy Room",
      requester: "Tom Iverson", channel: "#finance", status: "vetoed", at: 595,
      lines: [{ item: "Dinner, 6 covers, private room", qty: 1, unit: 240.0, vendor: "The Ivy Room" }],
      reasons: [{ rule: "vendor_allowlist", observed: "The Ivy Room", limit: "not on approved list" }],
      decision: { by: "Priya Raman", at: "10:02", note: "not a venue we have an account with" }
    },
    {
      id: "REQ-1029", agent: "desk", tag: "stationery", title: "Archive filing boxes", charter: "office", vendor: "Northgate Tools",
      requester: "Priya Raman", channel: "#office-ops", status: "captured", at: 571,
      lines: [{ item: "Archive box, double wall, 10 pack", qty: 1, unit: 42.0, vendor: "Northgate Tools" }],
      reasons: [], order: "7GH90241LK5510283", auth: "4WP66109QE2273048", capture: "2LM88130TR6620441"
    },
    {
      id: "REQ-1027", agent: "tools", tag: "software", title: "Papertrail storage top-up", charter: "software", vendor: "Papertrail CI",
      requester: "Mira Sandoval", channel: "#engineering", status: "captured", at: 544,
      lines: [{ item: "Papertrail 500 GB archive block", qty: 1, unit: 150.0, vendor: "Papertrail" }],
      reasons: [], order: "9QR31885BZ7740291", auth: "2ED77455VS1908426", capture: "6TY44702NB1188350"
    },
    {
      id: "REQ-1024", agent: "pantry", tag: "pantry", title: "Coffee run, Thursday", charter: "lunches", vendor: "Brightline Foods",
      requester: "Dan Okafor", channel: "#office-ops", status: "auto", at: 527,
      lines: [{ item: "Filter coffee, 12 cup, Thursday standup", qty: 1, unit: 46.0, vendor: "Brightline Foods" }],
      reasons: []
    },
    {
      id: "REQ-1021", agent: "desk", tag: "equipment", title: "Standing desk mat", charter: "office", vendor: "Ergo Depot",
      requester: "Oli Bennett", channel: "#studio", status: "vetoed", at: 510,
      lines: [{ item: "Anti-fatigue standing mat, 90 × 60 cm", qty: 1, unit: 89.0, vendor: "Ergo Depot" }],
      reasons: [{ rule: "vendor_allowlist", observed: "Ergo Depot", limit: "not on approved list" }],
      decision: { by: "Priya Raman", at: "09:12", note: "samples only, order from Meridian" }
    },
    {
      id: "REQ-1018", agent: "desk", tag: "stationery", title: "Whiteboard markers", charter: "office", vendor: "Meridian Paper",
      requester: "Dan Okafor", channel: "#office-ops", status: "auto", at: 492,
      lines: [{ item: "Whiteboard marker, assorted, 4 pack", qty: 2, unit: 9.4, vendor: "Meridian Paper" }],
      reasons: []
    }
  ]
};


/* ---- agents: the unit of authority -------------------------------------
   An agent is a named scope that a slash command opens. It owns a slice of
   one charter, the rules it is allowed to apply, and the tags its requests
   carry into analytics. Nothing is bought outside an agent. */
const AGENTS = [
  { id: "pantry", name: "Pantry runner", charter: "office", channel: "#office-ops", owner: "Dan Okafor",
    scope: "Coffee, paper goods and cleaning for the studio",
    rules: ["per_unit_cap", "approval_threshold", "vendor_allowlist"],
    tags: ["pantry", "recurring"], vendorScope: ["Kettle Supply"] },
  { id: "desk", name: "Desk kit", charter: "office", channel: "#studio", owner: "Mira Sandoval",
    scope: "Equipment and stationery for desks and new starters",
    rules: ["per_order_cap", "approval_threshold", "vendor_allowlist"],
    tags: ["equipment", "stationery"], vendorScope: ["Meridian Paper", "Northgate Tools"] },
  { id: "lunch", name: "Client lunch", charter: "lunches", channel: "#finance", owner: "Tom Iverson",
    scope: "Lunches and dinners with clients",
    rules: ["vendor_allowlist", "approval_threshold"],
    tags: ["hospitality"], vendorScope: ["Brightline Foods"] },
  { id: "ship", name: "Sample post", charter: "courier", channel: "#studio", owner: "Oli Bennett",
    scope: "Courier runs and return labels for sample boxes",
    rules: ["per_order_cap", "approval_threshold"],
    tags: ["shipping"], vendorScope: ["Swift Courier"] },
  { id: "tools", name: "Tooling", charter: "software", channel: "#engineering", owner: "Tom Iverson",
    scope: "Software renewals, seats and build minutes",
    rules: ["monthly_cap", "per_order_cap"],
    tags: ["software", "recurring"], vendorScope: ["Northwind Analytics", "Ferrite CI", "Papertrail"] }
];

/* The catalogue an agent searches inside the Slack command. An item is only
   offered when it clears the agent's own rules, so the search is the rule set. */
const CATALOGUE = [
  { sku: "caps-30",   name: "Vertuo Intenso capsules, 30 ct",   vendor: "Kettle Supply",      category: "pantry",     unit: 18.00 },
  { sku: "tp-24",     name: "Bamboo toilet paper, 24 rolls",     vendor: "Kettle Supply",      category: "pantry",     unit: 14.50 },
  { sku: "towels-12", name: "Paper towels, 12 rolls",            vendor: "Meridian Paper",     category: "pantry",     unit: 11.20 },
  { sku: "beans-1kg", name: "Single origin beans, 1 kg",         vendor: "Kettle Supply",      category: "pantry",     unit: 26.00 },
  { sku: "soap-5l",   name: "Dish soap, 5 L",                    vendor: "Kettle Supply",      category: "pantry",     unit: 16.40 },
  { sku: "marker-4",  name: "Whiteboard marker, 4 pack",         vendor: "Meridian Paper",     category: "stationery", unit: 9.40 },
  { sku: "a4-5",      name: "A4 copy paper, 5 reams",            vendor: "Meridian Paper",     category: "stationery", unit: 28.40 },
  { sku: "notes-12",  name: "Sticky notes, 12 pads",             vendor: "Meridian Paper",     category: "stationery", unit: 8.60 },
  { sku: "recycled-5", name: "Recycled copy paper, 5 reams",     vendor: "Meridian Paper",     category: "stationery", unit: 31.00 },
  { sku: "kbd-split", name: "Split ergonomic keyboard",          vendor: "Northgate Tools",    category: "equipment",  unit: 128.00 },
  { sku: "mat-stand", name: "Anti-fatigue standing mat, 90 x 60", vendor: "Ergo Depot",        category: "equipment",  unit: 89.00 },
  { sku: "arm-single", name: "Monitor arm, single",              vendor: "Northgate Tools",    category: "equipment",  unit: 74.00 },
  { sku: "stand-alu", name: "Laptop stand, aluminium",           vendor: "Northgate Tools",    category: "equipment",  unit: 42.00 },
  { sku: "dock-11",   name: "USB-C dock, 11-port",               vendor: "Northgate Tools",    category: "equipment",  unit: 189.00 }
];

const RULE_LABEL = {
  monthly_cap: "Monthly cap", per_order_cap: "Per order cap", per_unit_cap: "Per unit cap",
  approval_threshold: "Approval threshold", vendor_allowlist: "Vendor allowlist",
  category_allowlist: "Category allowlist", catalogue: "Catalogue match"
};


/* Three more nav glyphs, drawn to the same 16px / 1.5px grid as the rest. */
const EXTRA_ICONS = {
  agents: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3.2" y="4.6" width="9.6" height="8" rx="2.2"/><path d="M8 2.6v2"/><circle cx="6.2" cy="8.2" r=".75" fill="currentColor" stroke="none"/><circle cx="9.8" cy="8.2" r=".75" fill="currentColor" stroke="none"/><path d="M6.4 10.6h3.2"/></svg>',
  catalogue: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M7.4 2.6h4.4a1 1 0 0 1 1 1v4.4a1 1 0 0 1-.3.7l-4.8 4.8a1 1 0 0 1-1.4 0L2.9 10a1 1 0 0 1 0-1.4l3.8-3.8a1 1 0 0 1 .7-.2z"/><circle cx="10.1" cy="5.6" r=".9"/></svg>',
  analytics: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.6 12.4V3.4"/><path d="M2.6 12.4h10.8"/><path d="M4.8 9.8l2.6-2.9 2.3 1.9 3-3.6"/></svg>'
};
