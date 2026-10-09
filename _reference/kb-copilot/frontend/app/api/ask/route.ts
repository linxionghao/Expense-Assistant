import { NextResponse } from "next/server";

type ErrorResponse = { error: string };

export async function POST(req: Request) {
  try {
    const body = (await req.json()) as unknown;
    const baseUrl = process.env.API_BASE_URL || "http://localhost:8000";

    const r = await fetch(`${baseUrl}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const data = (await r.json()) as unknown;
    return NextResponse.json(data, { status: r.status });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : "Unknown error";
    const payload: ErrorResponse = { error: msg };
    return NextResponse.json(payload, { status: 500 });
  }
}
