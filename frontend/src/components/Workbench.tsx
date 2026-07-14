import { Sidebar } from "./Sidebar";
import { Reader } from "./Reader";
import { ChatPanel } from "./ChatPanel";
import { useUI } from "../store";

/** The paper-reading module: 文库 list | reader | 领航 chat. One tool of the Pharos platform. */
export function Workbench() {
  const chatOpen = useUI((s) => s.chatOpen);
  return (
    <div className={`workbench${chatOpen ? " chat-open" : ""}`}>
      <Sidebar />
      <Reader />
      {chatOpen && <ChatPanel />}
    </div>
  );
}
