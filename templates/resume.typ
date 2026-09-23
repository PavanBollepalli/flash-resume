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
#let font-size = if density == "tight" { 8.5pt } else if density == "compact" { 9.0pt } else { 9.4pt }
#let line-spacing = if density == "tight" { 0.46em } else if density == "compact" { 0.50em } else { 0.56em }
#let section-spacing = if density == "tight" { 0.42em } else if density == "compact" { 0.58em } else { 0.72em }
#let item-spacing = if density == "tight" { 0.18em } else if density == "compact" { 0.25em } else { 0.34em }

#let resume = json(data-path)
#let contact = resume.at("contact", default: (:))
#let contact-name = contact.at("name", default: "")
#let contact-location = contact.at("location", default: "")
#let contact-phone = contact.at("phone", default: "")

#set document(
  author: contact-name,
  title: contact-name + " - Resume",
)

#let page-margin-v = if density == "tight" { 0.30in } else if density == "compact" { 0.42in } else { 0.55in }
#let page-margin-h = if density == "tight" { 0.42in } else if density == "compact" { 0.58in } else { 0.68in }

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
)

#let section-header(title) = {
  v(section-spacing)
  block(width: 100%)[
    #text(weight: "bold", size: font-size + 1.1pt)[#upper(title)]
    #v(-0.28em)
    #line(length: 100%, stroke: 0.55pt + black)
  ]
  v(0.18em)
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
    #contact-link(contact.at("linkedin", default: none), "LinkedIn")
    #contact-link(contact.at("github", default: none), "GitHub")
    #contact-link(contact.at("portfolio", default: none), "Portfolio")
  ]
  #v(3pt)
]

#if resume.at("summary", default: none) != none and resume.summary != "" [
  #section-header("Summary")
  #text(size: font-size)[#resume.summary]
]

#if resume.at("highlights", default: ()).len() > 0 [
  #v(2pt)
  #text(size: font-size)[
    #resume.highlights.map(item => strong[#item]).join[ #h(0.6em) | #h(0.6em) ]
  ]
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
        #text(style: "italic", size: font-size - 0.45pt)[#exp.location]
      ]
      #list(
        marker: [•],
        spacing: 0.20em,
        ..exp.bullets.map(b => text()[#b])
      )
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
          #if proj.at("link", default: none) != none and proj.link != "" [
            #text(size: font-size - 0.45pt)[#link(proj.link)[Project link]]
          ]
        ]
      )
      #if proj.at("technologies", default: ()).len() > 0 [
        #text(style: "italic", size: font-size - 0.45pt)[#proj.technologies.join(", ")]
      ]
      #v(1pt)
      #list(
        marker: [•],
        spacing: 0.20em,
        ..proj.bullets.map(b => text()[#b])
      )
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
  #if resume.at("certifications", default: ()).len() > 0 [
    #v(2pt)
    #text(size: font-size - 0.3pt)[Certifications: #resume.certifications.join(", ")]
  ]
]

#context {
  let total = counter(page).final().first()
  if total > 1 [
    #v(1em)
    #text(fill: red, weight: "bold", size: 9pt)[
      ⚠ OVERFLOW: this resume is #total pages at density="#density". Cut content or use a denser density.
    ]
  ]
}
