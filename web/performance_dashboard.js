import { app } from "../../scripts/app.js";
import { mountDashboard, unmountDashboard } from "./dashboard.js";

const TAB_ID = "performance-report";

const stylesheet = document.createElement("link");
stylesheet.rel = "stylesheet";
stylesheet.href = new URL("./dashboard.css", import.meta.url).href;
document.head.append(stylesheet);

app.registerExtension({
  name: "Comfy.PerformanceReport.Dashboard",
  // Sits next to Logs in the bottom panel
  bottomPanelTabs: [
    {
      id: TAB_ID,
      title: "Performance",
      type: "custom",
      render: mountDashboard,
      destroy: unmountDashboard,
    },
  ],
  actionBarButtons: [
    {
      icon: "icon-[lucide--gauge] size-4",
      tooltip: "Performance dashboard",
      onClick: () => app.extensionManager.command.execute(`Workspace.ToggleBottomPanelTab.${TAB_ID}`),
    },
  ],
});
