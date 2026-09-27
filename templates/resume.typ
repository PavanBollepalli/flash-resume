// ---------------------------------------------------------------------
// Data-driven resume template.
// Visual structure ported verbatim from the approved static resume.typ
// (all-caps section headers, tilde-separated contact line, two-line
// grid headers for education/experience/projects, grouped
// certifications). Input contract (data_path / density / compact /
// lint) matches the app's existing template so nothing on the calling
// side needs to change.
//
// Compile with:
//   typst compile template.typ output.pdf \
//     --input data_path=resume.json \
//     --input density=tight|compact|standard \
//     --input lint=true   (optional, review-only warnings)
// ---------------------------------------------------------------------

#let data-path = sys.inputs.at("data_path", default: "resume.json")
#assert(
  not data-path.contains(".."),
  message: "data_path must not contain '..' (path traversal risk)",
)
#let allowed-densities = ("tight", "compact", "standard")
#let legacy-compact = sys.inputs.at("compact", default: "false") == "true"
#let density = sys.inputs.at("density", default: if legacy-compact { "compact" } else { "standard" })
#assert(
  allowed-densities.contains(density),
  message: "Unknown density '" + density + "'. Expected one of: " + allowed-densities.join(", "),
)

// "tight" reproduces the exact hand-tuned one-page constants from the
// approved static file. compact/standard loosen progressively, same
// density-fallback pattern as the rest of the pipeline.
#let font-size = if density == "tight" { 9.6pt } else if density == "compact" { 10pt } else { 10.5pt }
#let line-spacing = if density == "tight" { 0.42em } else if density == "compact" { 0.50em } else { 0.58em }
#let section-spacing = if density == "tight" { 0.18em } else if density == "compact" { 0.30em } else { 0.42em }
#let item-spacing = if density == "tight" { 0.38em } else if density == "compact" { 0.45em } else { 0.55em }
#let para-spacing = if density == "tight" { 0.55em } else if density == "compact" { 0.65em } else { 0.75em }

#let resume = json(data-path)
#let contact = resume.at("contact", default: (:))
#let contact-name = contact.at("name", default: "")

#set document(
  author: contact-name,
  title: contact-name + " - Resume",
)

#let page-margin-v = if density == "tight" { 1.1cm } else if density == "compact" { 1.3cm } else { 1.5cm }
#let page-margin-h = if density == "tight" { 1.5cm } else if density == "compact" { 1.7cm } else { 1.9cm }

#set page(
  margin: (top: page-margin-v, bottom: page-margin-v, left: page-margin-h, right: page-margin-h),
)

#set text(font: "New Computer Modern", size: font-size, lang: "en")
#set par(justify: true, leading: line-spacing, spacing: para-spacing)
#set block(spacing: para-spacing)

#let sectionTitle(title) = [
  #v(section-spacing)
  #text(size: font-size + 1.4pt, weight: "bold")[#upper(title)]
  #line(length: 100%, stroke: 0.6pt)
  #v(0.1em)
]

#let sep = [ ~ ]

// ---------------------------- Header ----------------------------
#align(center)[
  #text(size: font-size + 6.4pt, weight: "bold", tracking: 0.5pt)[#upper(contact-name)]
  #v(0.15em)
  #text(size: font-size - 0.1pt)[
    #contact.at("location", default: "")#sep#contact.at("phone", default: "")
    #if contact.at("email", default: "") != "" [
      #sep#link("mailto:" + contact.email)[#contact.email]
    ]
    #if contact.at("linkedin", default: none) != none and contact.linkedin != "" [
      #sep#link("https://" + contact.linkedin.trim("https://").trim("http://").trim("/"))[LinkedIn]
    ]
    #if contact.at("portfolio", default: none) != none and contact.portfolio != "" [
      #sep#link("https://" + contact.portfolio.trim("https://").trim("http://").trim("/"))[#contact.portfolio.trim("https://").trim("http://").trim("/")]
    ]
    #if contact.at("github", default: none) != none and contact.github != "" [
      #sep#link("https://" + contact.github.trim("https://").trim("http://").trim("/"))[GitHub]
    ]
  ]
]

// ---------------------------- Summary ----------------------------
#if resume.at("summary", default: none) != none and resume.summary != "" [
  #sectionTitle[Summary]
  #text(size: font-size)[#resume.summary]
]

// ---------------------------- Skills ----------------------------
#if resume.at("skills", default: ()).len() > 0 [
  #sectionTitle[Technical Skills]
  #grid(
    columns: (auto, 1fr),
    column-gutter: 0.6em,
    row-gutter: 0.45em,
    ..resume.skills.map(s => (
      [*#s.category:*], [#s.items.join(", ")],
    )).flatten()
  )
]

// ---------------------------- Education ----------------------------
// score_label/score_value covers both "CGPA - 8.56/10.0" and
// "Score - 929/1000" style lines without forcing one vocabulary; falls
// back to a plain gpa string if that's all the data has.
#if resume.at("education", default: ()).len() > 0 [
  #sectionTitle[Education]
  #for (i, edu) in resume.education.enumerate() [
    #if i > 0 [#v(0.15em)]
    #grid(
      columns: (1fr, auto),
      [*#edu.institution*], [#edu.start_date -- #edu.end_date],
    )
    #grid(
      columns: (1fr, auto),
      [
        #edu.degree
        #if edu.at("score_label", default: none) != none and edu.at("score_value", default: none) != none [
          --- #edu.score_label - #edu.score_value
        ] else if edu.at("gpa", default: none) != none [
          --- CGPA - #edu.gpa
        ]
      ],
      [#edu.at("location", default: "")],
    )
  ]
]

// ---------------------------- Experience ----------------------------
#if resume.at("experience", default: ()).len() > 0 [
  #sectionTitle[Experience]
  #for (i, exp) in resume.experience.enumerate() [
    #if i > 0 [#v(0.25em)]
    #grid(
      columns: (1fr, auto),
      [*#exp.company*], [#exp.at("location", default: "")],
    )
    #grid(
      columns: (1fr, auto),
      [#emph(exp.role)], [#exp.start_date -- #exp.end_date],
    )
    #v(0.2em)
    #list(
      spacing: item-spacing,
      ..exp.bullets.map(b => [#b])
    )
  ]
]

// ---------------------------- Projects ----------------------------
#if resume.at("projects", default: ()).len() > 0 [
  #sectionTitle[Projects]
  #for (i, proj) in resume.projects.enumerate() [
    #if i > 0 [#v(0.25em)]
    #grid(
      columns: (1fr, auto),
      [*#proj.name*],
      [
        #if proj.at("github", default: none) != none and proj.github != "" [
          #link(proj.github)[GitHub]
        ] else if proj.at("live_url", default: none) != none and proj.live_url != "" [
          #link(proj.live_url)[Live]
        ] else if proj.at("link", default: none) != none and proj.link != "" [
          #link(proj.link)[Link]
        ]
      ],
    )
    #list(
      spacing: item-spacing,
      ..proj.bullets.map(b => [#b]),
      ..if proj.at("technologies", default: ()).len() > 0 {
        ([*Tech Stack:* #proj.technologies.join(", ").] ,)
      } else { () }
    )
  ]
]

// ---------------------------- Certifications ----------------------------
// Prefer grouped-by-category rendering (the model's certification_groups
// field, populated at parse time): one bold category line per group with
// comma-separated certs. Falls back to a plain bullet list when the data
// only supplies the flat string list, so older data shapes stay readable.
#if resume.at("certification_groups", default: ()).len() > 0 [
  #sectionTitle[Certificates]
  #list(
    spacing: item-spacing,
    ..resume.certification_groups.map(g => [*#g.category:* #g.items.join(", ")])
  )
] else if resume.at("certifications", default: ()).len() > 0 [
  #sectionTitle[Certificates]
  #list(
    spacing: item-spacing,
    ..resume.certifications.map(c => [#c])
  )
]

#if resume.at("awards", default: ()).len() > 0 [
  #sectionTitle[Awards]
  #resume.awards.join("  |  ")
]

// -----------------------------------------------------------------
// Content lint + overflow detection -- unchanged from the app's
// existing template, same opt-in flag and thresholds.
// -----------------------------------------------------------------
#let lint-enabled = sys.inputs.at("lint", default: "false") == "true"
#let max-bullet-chars = 180
#let lint-warnings = if lint-enabled {
  let warnings = ()
  for exp in resume.at("experience", default: ()) {
    if exp.bullets.len() < 2 {
      warnings.push("Experience \"" + exp.role + "\" has only " + str(exp.bullets.len()) + " bullet(s) -- uneven against sibling entries.")
    }
    for b in exp.bullets {
      if b.len() > max-bullet-chars {
        warnings.push("Bullet in \"" + exp.role + "\" is " + str(b.len()) + " chars -- likely stacking multiple claims, consider splitting.")
      }
    }
  }
  for proj in resume.at("projects", default: ()) {
    if proj.bullets.len() < 2 {
      warnings.push("Project \"" + proj.name + "\" has only " + str(proj.bullets.len()) + " bullet(s) -- uneven against sibling entries.")
    }
    for b in proj.bullets {
      if b.len() > max-bullet-chars {
        warnings.push("Bullet in \"" + proj.name + "\" is " + str(b.len()) + " chars -- likely stacking multiple claims, consider splitting.")
      }
    }
  }
  warnings
} else {
  ()
}

#if lint-warnings.len() > 0 [
  #v(1em)
  #for w in lint-warnings [
    #text(fill: red, size: 8pt)[⚠ #w] \
  ]
]

#context {
  let total = counter(page).final().first()
  if total > 1 {
    if lint-enabled [
      #v(1em)
      #text(fill: red, weight: "bold", size: 9pt)[
        ⚠ OVERFLOW: this resume is #total pages at density="#density". Cut content or use a denser density.
      ]
    ]
  }
}
