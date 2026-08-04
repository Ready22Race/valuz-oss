import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@openuidev/react-ui", () => ({
  AreaChartCondensed: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="area-chart">{JSON.stringify(data)}</div>
  ),
  BarChartCondensed: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="bar-chart">{JSON.stringify(data)}</div>
  ),
  Button: ({ children }: { children: ReactNode }) => <button>{children}</button>,
  Buttons: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Callout: ({
    title,
    description,
  }: {
    title?: ReactNode;
    description?: ReactNode;
  }) => (
    <aside>
      {title}
      {description}
    </aside>
  ),
  Card: ({ children }: { children: ReactNode }) => <section>{children}</section>,
  CardHeader: ({
    title,
    subtitle,
  }: {
    title?: ReactNode;
    subtitle?: ReactNode;
  }) => (
    <header>
      <h2>{title}</h2>
      <p>{subtitle}</p>
    </header>
  ),
  CodeBlock: ({ codeString }: { codeString: string }) => <code>{codeString}</code>,
  FormControl: ({ children }: { children: ReactNode }) => <label>{children}</label>,
  HorizontalBarChart: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="horizontal-chart">{JSON.stringify(data)}</div>
  ),
  Image: ({ src, alt }: { src: string; alt?: string }) => (
    <img src={src} alt={alt} />
  ),
  ImageBlock: ({ src, alt }: { src: string; alt?: string }) => (
    <img src={src} alt={alt} />
  ),
  ImageGallery: ({
    images,
  }: {
    images: { src: string; alt?: string }[];
  }) => (
    <div>
      {images.map((image) => (
        <img key={image.src} src={image.src} alt={image.alt} />
      ))}
    </div>
  ),
  Input: (props: { placeholder?: string }) => <input {...props} />,
  Label: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  LineChartCondensed: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="line-chart">{JSON.stringify(data)}</div>
  ),
  MarkDownRenderer: ({ textMarkdown }: { textMarkdown: string }) => (
    <div>{textMarkdown}</div>
  ),
  PieChart: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="pie-chart">{JSON.stringify(data)}</div>
  ),
  RadarChart: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="radar-chart">{JSON.stringify(data)}</div>
  ),
  RadialChart: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="radial-chart">{JSON.stringify(data)}</div>
  ),
  ScatterChart: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="scatter-chart">{JSON.stringify(data)}</div>
  ),
  ScrollableTable: ({ children }: { children: ReactNode }) => (
    <table>{children}</table>
  ),
  Select: ({ children }: { children: ReactNode }) => <select>{children}</select>,
  SelectContent: ({ children }: { children: ReactNode }) => <>{children}</>,
  SelectItem: ({
    children,
    value,
  }: {
    children: ReactNode;
    value: string;
  }) => <option value={value}>{children}</option>,
  SelectTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
  SelectValue: ({ placeholder }: { placeholder?: string }) => <>{placeholder}</>,
  Separator: () => <hr />,
  SingleStackedBar: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="stacked-chart">{JSON.stringify(data)}</div>
  ),
  SliderBlock: ({ label }: { label: string }) => <div>{label}</div>,
  TableBody: ({ children }: { children: ReactNode }) => <tbody>{children}</tbody>,
  TableCell: ({ children }: { children: ReactNode }) => <td>{children}</td>,
  TableHead: ({ children }: { children: ReactNode }) => <th>{children}</th>,
  TableHeader: ({ children }: { children: ReactNode }) => <thead>{children}</thead>,
  TableRow: ({ children }: { children: ReactNode }) => <tr>{children}</tr>,
  Tag: ({ text }: { text: ReactNode }) => <span>{text}</span>,
  TagBlock: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  TextArea: (props: { placeholder?: string }) => <textarea {...props} />,
  TextCallout: ({
    title,
    description,
  }: {
    title?: ReactNode;
    description?: ReactNode;
  }) => (
    <aside>
      {title}
      {description}
    </aside>
  ),
  TextContent: ({ children }: { children: ReactNode }) => <p>{children}</p>,
}));

vi.mock("@openuidev/react-ui/Modal", () => ({
  Modal: ({ children, title }: { children: ReactNode; title: string }) => (
    <div role="dialog" aria-label={title}>
      {children}
    </div>
  ),
}));

import { A2UIRenderer } from "./A2UIRenderer";

describe("A2UIRenderer", () => {
  it("renders A2UI v0.9 streams with the OpenUI component mapping", () => {
    const messages = [
      {
        version: "v0.9",
        createSurface: { surfaceId: "dashboard", catalogId: "openui" },
      },
      {
        version: "v0.9",
        updateComponents: {
          surfaceId: "dashboard",
          components: [
            {
              id: "root",
              component: "Stack",
              children: ["header", "metric", "table", "chart"],
            },
            {
              id: "header",
              component: "CardHeader",
              title: "Catalog coverage",
              subtitle: "OpenUI aliases",
            },
            {
              id: "metric",
              component: "Metric",
              label: "Revenue",
              value: "$12.4M",
            },
            {
              id: "table",
              component: "Table",
              columns: [
                { component: "Col", label: "Region", data: ["North"] },
                { component: "Col", label: "Revenue", data: [12] },
              ],
            },
            {
              id: "chart",
              component: "BarChart",
              labels: ["Q1", "Q2"],
              series: [{ component: "Series", category: "Revenue", values: [10, 12] }],
            },
          ],
        },
      },
    ]
      .map((message) => JSON.stringify(message))
      .join("\n");

    render(<A2UIRenderer body={messages} />);

    expect(screen.getByText("Catalog coverage")).toBeTruthy();
    expect(screen.getAllByText("Revenue")).toHaveLength(2);
    expect(screen.getByText("$12.4M")).toBeTruthy();
    expect(screen.getByText("Region")).toBeTruthy();
    expect(screen.getByText("North")).toBeTruthy();
    expect(screen.getByTestId("bar-chart").textContent).toContain("Q1");
  });

  it("accepts legacy nested props and inline child component objects", () => {
    const messages = [
      {
        version: "v0.9",
        createSurface: { surfaceId: "legacy", catalogId: "valuz" },
      },
      {
        version: "v0.9",
        updateComponents: {
          surfaceId: "legacy",
          components: [
            {
              id: "root",
              component: "Stack",
              props: { direction: "column" },
              children: [
                {
                  component: "TextContent",
                  props: { text: "Legacy payload" },
                },
              ],
            },
          ],
        },
      },
    ]
      .map((message) => JSON.stringify(message))
      .join("\n");

    render(<A2UIRenderer body={messages} />);

    expect(screen.getByText("Legacy payload")).toBeTruthy();
  });

  it("infers the active surface when adding a missing root component", () => {
    const messages = [
      {
        version: "v0.9",
        createSurface: { surfaceId: "custom-surface", catalogId: "openui" },
      },
      {
        version: "v0.9",
        updateComponents: {
          surfaceId: "custom-surface",
          components: [
            {
              id: "summary",
              component: "TextContent",
              text: "Rendered without an explicit root",
            },
          ],
        },
      },
    ]
      .map((message) => JSON.stringify(message))
      .join("\n");

    render(<A2UIRenderer body={messages} />);

    expect(screen.getByText("Rendered without an explicit root")).toBeTruthy();
  });
});
