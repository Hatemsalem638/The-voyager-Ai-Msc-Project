import { ChatBox } from "../components/ChatBox";
import { useSearchParams } from "react-router-dom";

export function VoyagerPage() {
  const [searchParams] = useSearchParams();
  const prompt = searchParams.get("prompt") ?? "";

  return (
    <section className="page-container voyager-page">
      <div className="page-heading">
        <p className="eyebrow">Voyage AI companion</p>
        <h1>The Voyager</h1>
        <p>
          Ask The Voyager to search flights, compare hotels, check options, and
          prepare bookings for confirmation.
        </p>
      </div>
      <ChatBox initialPrompt={prompt} />
    </section>
  );
}
