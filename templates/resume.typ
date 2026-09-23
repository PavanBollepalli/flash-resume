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
#let font-size = 10.5pt
#let line-spacing = if density == "tight" { 0.62em } else if density == "compact" { 0.70em } else { 0.78em }
#let section-spacing = if density == "tight" { 0.42em } else if density == "compact" { 0.58em } else { 0.72em }
#let item-spacing = if density == "tight" { 0.18em } else if density == "compact" { 0.25em } else { 0.34em }
// Separate token for paragraph-level breaks (title -> location -> tech
// line) -- deliberately larger than item-spacing (bullet-to-bullet) so
// the two kinds of gap stay visually distinct, but nowhere near Typst's
// oversized ~1.2em default that caused the original "Remote" floating gap.
#let para-spacing = if density == "tight" { 0.30em } else if density == "compact" { 0.40em } else { 0.50em }

#let resume = json(data-path)
#let contact = resume.at("contact", default: (:))
#let contact-name = contact.at("name", default: "")
#let contact-location = contact.at("location", default: "")
#let contact-phone = contact.at("phone", default: "")

#set document(
  author: contact-name,
  title: contact-name + " - Resume",
)

#let page-margin-v = if density == "tight" { 0.50in } else if density == "compact" { 0.55in } else { 0.60in }
#let page-margin-h = if density == "tight" { 0.50in } else if density == "compact" { 0.60in } else { 0.68in }

#set page(
  paper: "a4",
  margin: (
    top: page-margin-v,
    bottom: page-margin-v,
    left: page-margin-h,
    right: page-margin-h,
  ),
)

#set text(
  size: font-size,
  font: ("Liberation Serif", "Times New Roman", "DejaVu Serif"),
  fill: black,
  spacing: 100%,
  lang: "en",
)

#set par(
  leading: line-spacing,
  justify: true,
  // Explicitly tie inter-paragraph spacing to the same density scale as
  // everything else. Left unset, Typst falls back to its own default
  // (~1.2em), which is why the title/location/tech-line transitions had
  // a much bigger gap than the deliberately tight list-item spacing
  // right below them -- two unrelated spacing values fighting inside
  // what should read as one visually consistent block.
  spacing: para-spacing,
)

#let section-header(title) = {
  v(section-spacing)
  text(weight: "bold", size: font-size + 1.1pt)[#upper(title)]
  v(3pt, weak: true)
  line(length: 100%, stroke: 0.55pt + black)
  v(item-spacing)
}

#let contact-link(url, label) = {
  if url != none and url != "" {
    let clean = url.trim("https://").trim("http://").trim("/")
    [#h(0.35em) | #h(0.35em) #link("https://" + clean)[#label]]
  }
}

#align(center)[
  #text(weight: "bold", size: font-size + 6pt)[#contact-name] \
  #if contact.at("title", default: none) != none [
    #v(1pt)
    #text(weight: "bold", size: font-size + 0.5pt)[#contact.title] \
  ]
  #v(2pt)
  #text(size: font-size - 0.45pt)[
    #contact-location #h(0.35em) | #h(0.35em) #contact-phone
    #if contact.at("email", default: "") != "" [
      #h(0.35em) | #h(0.35em) #link("mailto:" + contact.email)[#contact.email]
    ]
    #contact-link(contact.at("linkedin", default: none), contact.at("linkedin", default: ""))
    #contact-link(contact.at("github", default: none), contact.at("github", default: ""))
    #contact-link(contact.at("portfolio", default: none), contact.at("portfolio", default: ""))
  ]
  #v(3pt)
]

#if resume.at("summary", default: none) != none and resume.summary != "" [
  #section-header("Summary")
  #text(size: font-size)[#resume.summary]
]

#if resume.at("skills", default: ()).len() > 0 [
  #section-header("Skills")
  #for skill in resume.skills [
    #text(weight: "bold")[#skill.category:] #skill.items.join(", ") \
  ]
]

#if resume.at("experience", default: ()).len() > 0 [
  #section-header("Experience")
  #for exp in resume.experience [
    #block(below: item-spacing)[
      #grid(
        columns: (1fr, auto),
        align: (left, right),
        [
          #text(weight: "bold")[#exp.role -- #exp.company]
        ],
        [
          #text(weight: "bold")[#exp.start_date -- #exp.end_date]
        ]
      )
      #if exp.at("location", default: "") != "" [
        #block(above: 2pt, below: 3pt)[#text(style: "italic", size: font-size - 0.45pt)[#exp.location]]
      ]
      #for bullet in exp.bullets [
        #block[
          #grid(
            columns: (0.16in, 1fr),
            gutter: 0.05in,
            [•],
            [#bullet],
          )
        ]
        #v(4pt)
      ]
    ]
  ]
]

#if resume.at("projects", default: ()).len() > 0 [
  #section-header("Projects")
  #for proj in resume.projects [
    #block(below: item-spacing)[
      #grid(
        columns: (1fr, auto),
        align: (left, right),
        [
          #text(weight: "bold")[#proj.name]
        ],
        [
          #if proj.at("start_date", default: none) != none and proj.at("end_date", default: none) != none [
            #text(weight: "bold")[#proj.start_date -- #proj.end_date]
          ]
          #if proj.at("github", default: none) != none and proj.github != "" [
            #h(0.35em) #link(proj.github)[GitHub]
          ]
          #if proj.at("live_url", default: none) != none and proj.live_url != "" [
            #h(0.35em) #link(proj.live_url)[Live]
          ]
          #if proj.at("github", default: none) == none and proj.at("live_url", default: none) == none and proj.at("link", default: none) != none and proj.link != "" [
            #h(0.35em) #link(proj.link)[Link]
          ]
        ]
      )
      #if proj.at("technologies", default: ()).len() > 0 [
        #text(style: "italic", size: font-size - 0.45pt)[#proj.technologies.join(", ")]
      ]
      #v(1pt)
      #for bullet in proj.bullets [
        #block[
          #grid(
            columns: (0.16in, 1fr),
            gutter: 0.05in,
            [•],
            [#bullet],
          )
        ]
        #v(4pt)
      ]
    ]
  ]
]

#if resume.at("education", default: ()).len() > 0 [
  #section-header("Education")
  #for edu in resume.education [
    #grid(
      columns: (1fr, auto),
      align: (left, right),
      [
        #text(weight: "bold")[#edu.degree, #edu.field_of_study]
        #linebreak()
        #text(style: "italic")[#edu.institution]
        #if edu.at("gpa", default: none) != none [ (CGPA: #edu.gpa)]
      ],
      [#edu.start_date -- #edu.end_date],
    )
  ]
]

#if resume.at("awards", default: ()).len() > 0 or resume.at("certifications", default: ()).len() > 0 [
  #section-header("Awards & Certifications")
  #let award-items = resume.at("awards", default: ()) + resume.at("certifications", default: ())
  #award-items.join("  |  ")
]

// Content lint: things a static schema can't enforce but shouldn't ship
// silently -- thin entries and run-on bullets get flagged the same way
// overflow does. GATED behind an explicit opt-in flag, defaulting to off,
// so a normal compile (what actually gets submitted to a recruiter) is
// always clean. Pass --input lint=true to see warnings during review.
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

// Overflow is a different class of problem -- it's not a content-quality
// opinion, it's "this file violates the one-page spec." In lint mode,
// show it as a warning for review. Outside lint mode -- i.e. a normal
// production compile -- fail the build entirely rather than silently
// shipping a 2-page PDF with a red banner on it. A submittable resume
// must never be the thing that reveals its own defect to the recruiter.
#context {
  let total = counter(page).final().first()
  if total > 1 {
    if lint-enabled [
      #v(1em)
      #text(fill: red, weight: "bold", size: 9pt)[
        ⚠ OVERFLOW: this resume is #total pages at density="#density". Cut content or use a denser density.
      ]
    ] else {
      panic("OVERFLOW: resume is " + str(total) + " pages at density=\"" + density + "\". Refusing to produce output -- reduce content or pass a denser --input density.")
    }
  }
}
