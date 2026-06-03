---
layout: default
title: Blog
permalink: /blog/
---

# Blog

Notes on autonomous test generation, the 5-signal quality verdict, and what we
learn building PFactory.

<ul class="post-list">
{% for post in site.posts %}
  <li class="post-list__item">
    <a class="post-list__link" href="{{ post.url | relative_url }}">
      <span class="post-list__date">{{ post.date | date: "%b %-d, %Y" }}</span>
      <span class="post-list__title">{{ post.title }}</span>
      {% if post.subtitle %}<span class="post-list__excerpt">{{ post.subtitle }}</span>
      {% elsif post.excerpt %}<span class="post-list__excerpt">{{ post.excerpt | strip_html | truncate: 150 }}</span>{% endif %}
    </a>
  </li>
{% endfor %}
</ul>

{% if site.posts == empty %}*No posts yet — check back soon.*{% endif %}
