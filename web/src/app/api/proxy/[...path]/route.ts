import { NextRequest, NextResponse } from "next/server";

const CONTROLLER_URL = process.env.CONTROLLER_URL ?? "http://controller:8000";

async function proxyRequest(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  const { path } = await params;
  const targetPath = path.join("/");
  const url = new URL(request.url);
  const search = url.search;
  const targetUrl = `${CONTROLLER_URL}/${targetPath}${search}`;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    // Skip hop-by-hop headers and host
    if (
      !["host", "connection", "keep-alive", "transfer-encoding"].includes(
        key.toLowerCase(),
      )
    ) {
      headers.set(key, value);
    }
  });

  const init: RequestInit = {
    method: request.method,
    headers,
  };

  if (request.method !== "GET" && request.method !== "HEAD") {
    const body = await request.text();
    if (body) {
      init.body = body;
    }
  }

  try {
    const response = await fetch(targetUrl, init);

    const responseHeaders = new Headers();
    response.headers.forEach((value, key) => {
      if (
        !["transfer-encoding", "connection", "keep-alive"].includes(
          key.toLowerCase(),
        )
      ) {
        responseHeaders.set(key, value);
      }
    });

    const responseBody = await response.arrayBuffer();

    return new NextResponse(responseBody, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error("Proxy error:", error);
    return NextResponse.json(
      { error: "Failed to connect to controller" },
      { status: 502 },
    );
  }
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  return proxyRequest(request, context);
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  return proxyRequest(request, context);
}

export async function PUT(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  return proxyRequest(request, context);
}

export async function DELETE(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  return proxyRequest(request, context);
}
