import { Sidebar } from "./components/Sidebar";
import { Reader } from "./components/Reader";
import { ChatPanel } from "./components/ChatPanel";
import { useUI } from "./store";
import "./App.css";

export default function App() {
  const chatOpen = useUI((s) => s.chatOpen);

  return (
    <div className={`workbench${chatOpen ? " chat-open" : ""}`}>
      <Sidebar />
      <Reader />
      {chatOpen && <ChatPanel />}
    </div>
  );
}
