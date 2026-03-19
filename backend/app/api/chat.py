"""Educational chatbot API."""

from __future__ import annotations

from collections import defaultdict
import logging
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.models.chat import ChatMessage as ChatMessageModel
from backend.app.models.schedule import ScheduleEntry
from backend.app.models.subject import Subject
from backend.app.models.topic import Topic
from backend.app.schemas.chat import ChatAskRequest, ChatAskResponse, ChatHistoryItem, ChatMessage

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _looks_truncated_answer(text: str) -> bool:
    if not text:
        return False
    stripped = text.rstrip()
    if len(stripped) < 80:
        return False
    if stripped.endswith((":", "-", "*", "(", "/", "\\")):
        return True
    if re.search(r"[A-Za-z0-9][A-Za-z]{0,4}$", stripped) and not stripped.endswith((".", "!", "?", '"', "'")):
        return True
    unfinished_markers = [
        "here's an explanation of each algorithm",
        "**algorithm 3:",
        "the algorithm",
        "for example",
        "steps:",
        "key points:",
    ]
    lowered = stripped.lower()
    return any(lowered.endswith(marker) for marker in unfinished_markers)


def _merge_continuation(base: str, extra: str) -> str:
    if not base:
        return extra
    if not extra:
        return base
    merged = extra.lstrip()
    max_overlap = min(len(base), len(merged), 240)
    for size in range(max_overlap, 20, -1):
        if base[-size:] == merged[:size]:
            merged = merged[size:]
            break
    return f"{base.rstrip()}\n{merged.lstrip()}".strip()


def _extract_multi_topics(message: str) -> list[str]:
    query = (message or "").strip().lower()
    cleaned = query.replace("\n", " ")
    cleaned = re.sub(r"\b(explain|xplain|what is|what are|define|means)\b", " ", cleaned)
    cleaned = re.sub(r"\balgorithms?\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    if not cleaned:
        return []
    parts = re.split(r",|\band\b|/|\|", cleaned)
    topics: list[str] = []
    for part in parts:
        topic = part.strip(" ,-")
        if not topic:
            continue
        topic = re.sub(r"\bplease\b", " ", topic)
        topic = re.sub(r"\s+", " ", topic).strip()
        if topic == "a*":
            topic = "A*"
        elif "*" in topic:
            topic = topic.upper()
        if topic and topic not in topics:
            topics.append(topic)
    return topics[:5]


def _wants_detailed_answer(message: str) -> bool:
    query = f" {_normalize_educational_query(message)} "
    detail_terms = {
        "in detail",
        "detailed",
        "deep",
        "deeply",
        "elaborate",
        "full answer",
        "long answer",
        "explain deeply",
        "step by step",
    }
    return any(f" {term} " in query for term in detail_terms)


def _normalize_educational_query(text: str) -> str:
    normalized = f" {_normalize(text)} "
    replacements = {
        " sprinboot ": " spring boot ",
        " springboot ": " spring boot ",
        " exlpain ": " explain ",
        " exlain ": " explain ",
        " explian ": " explain ",
        " what si ": " what is ",
        " whatis ": " what is ",
        " whatare ": " what are ",
        " tyes of ": " types of ",
        " typse of ": " types of ",
        " algo ": " algorithm ",
        " alogrithm ": " algorithm ",
        " algorthm ": " algorithm ",
        " genarte ": " generate ",
        " craetion ": " creation ",
        " craete ": " create ",
        " creaton ": " creation ",
        " cdode ": " code ",
        " serach ": " search ",
        " kahns ": " kahn ",
        " kahan ": " kahn ",
        " torjans ": " trojan ",
        " trojans ": " trojan ",
        " complier ": " compiler ",
        " dl ": " deep learning ",
        " languagemodeling ": " language modeling ",
        " ktopological ": " topological ",
        " topological chart ": " topological sort ",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    return re.sub(r"\s+", " ", normalized).strip()


def _tokenize(text: str) -> set[str]:
    return {tok for tok in _normalize(text).split() if len(tok) >= 2}


def _subject_aliases(name: str) -> set[str]:
    normalized = _normalize(name)
    aliases = {normalized}
    code_match = re.match(r"^([a-z0-9 ]+?)\s+-\s+", normalized)
    if code_match:
        aliases.add(code_match.group(1).strip())
        aliases.add(normalized.split("-", 1)[1].strip())
    return {alias for alias in aliases if alias}


def _format_schedule_line(entry: ScheduleEntry) -> str:
    return (
        f"{entry.scheduled_date} {str(entry.start_time)[:5]}-{str(entry.end_time)[:5]}: "
        f"{entry.subject_name} - {entry.topic_name}"
    )


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_phrase = _normalize_educational_query(phrase)
    if not normalized_phrase:
        return False
    return f" {normalized_phrase} " in f" {_normalize_educational_query(text)} "


def _question_intent(message: str) -> str:
    query = _normalize_educational_query(message)
    if not query:
        return "other"
    if any(token in f" {query} " for token in [" code ", " generate ", " write ", " implement "]):
        return "code"
    if "difference between" in query or "compare" in query:
        return "compare"
    if "types of" in query:
        return "types"
    if "how do i prepare" in query or "how should i prepare" in query or "exam in one week" in query:
        return "exam_plan"
    if query.startswith("how ") or " how " in f" {query} ":
        return "how"
    if (
        query.startswith("what is")
        or query.startswith("define")
        or query.startswith("explain")
        or query.startswith("xplain")
        or " explain " in f" {query} "
        or " xplain " in f" {query} "
    ):
        return "explain"
    return "other"


DOMAIN_FRAMES: dict[str, dict[str, object]] = {
    "dbms": {
        "label": "DBMS",
        "core": ["data organization", "schema design", "queries", "consistency", "transactions"],
        "applications": ["storing structured records", "retrieving data efficiently", "maintaining integrity"],
        "study_tip": "define the concept, explain the purpose, mention the key rules or components, and give one table example",
        "suggestions": ["Explain normalization in DBMS", "What are joins in SQL?", "What is indexing in DBMS?"],
    },
    "ml": {
        "label": "machine learning",
        "core": ["learning from data", "patterns", "prediction", "training", "evaluation"],
        "applications": ["classification", "recommendation", "forecasting"],
        "study_tip": "write the definition, list the main types or steps, and add one real-world example",
        "suggestions": ["Types of machine learning", "Explain supervised learning", "What is reinforcement learning?"],
    },
    "nlp": {
        "label": "natural language processing",
        "core": ["language understanding", "text processing", "tokenization", "meaning extraction", "generation"],
        "applications": ["chatbots", "translation", "sentiment analysis"],
        "study_tip": "start with the definition, then mention major tasks and one application",
        "suggestions": ["What is tokenization?", "Explain language models", "What is parsing in NLP?"],
    },
    "dsa": {
        "label": "data structures and algorithms",
        "core": ["problem solving", "representation", "traversal", "ordering", "complexity"],
        "applications": ["task scheduling", "search", "optimization"],
        "study_tip": "explain the idea first, then the steps, time complexity, and one example",
        "suggestions": ["Explain graphs", "What is topological sort?", "Difference between BFS and DFS"],
    },
    "compiler": {
        "label": "compiler design",
        "core": ["lexical analysis", "syntax analysis", "semantic checks", "translation", "optimization"],
        "applications": ["program translation", "error detection", "code generation"],
        "study_tip": "write the phase or concept in order and state what each stage does",
        "suggestions": ["Phases of compiler", "What is lexical analysis?", "What is parsing?"],
    },
    "network": {
        "label": "computer networks",
        "core": ["communication", "layers", "protocols", "addressing", "routing"],
        "applications": ["device communication", "internet access", "resource sharing"],
        "study_tip": "define the concept, list the layers or components, and give one communication example",
        "suggestions": ["Explain OSI model", "What is TCP/IP?", "What is routing?"],
    },
    "math": {
        "label": "mathematics",
        "core": ["definition", "formula", "properties", "steps", "example"],
        "applications": ["problem solving", "proof", "modeling"],
        "study_tip": "state the formula or definition, explain each term, and solve one short example",
        "suggestions": ["Explain slope formula", "What is probability?", "What is a derivative?"],
    },
    "general": {
        "label": "education",
        "core": ["definition", "key idea", "main points", "example", "importance"],
        "applications": ["exam writing", "revision", "understanding the topic"],
        "study_tip": "start with a simple definition, then add 3 to 4 key points and one example",
        "suggestions": ["Explain the topic in simple words", "Give an exam answer", "Give one example"],
    },
}


def _extract_focus_phrase(message: str) -> str:
    query = _normalize_educational_query(message)
    prefixes = [
        "what is ",
        "what are ",
        "define ",
        "explain ",
        "types of ",
        "difference between ",
        "compare ",
        "how does ",
        "how do ",
        "how to ",
    ]
    focus = query
    for prefix in prefixes:
        if focus.startswith(prefix):
            focus = focus[len(prefix):]
            break
    focus = re.sub(r"\bplease\b", " ", focus)
    focus = re.sub(r"\s+", " ", focus).strip(" ?.-")
    return focus or query


def _infer_domain_key(message: str, subjects: list[Subject]) -> str:
    query = _normalize_educational_query(message)
    checks = [
        ("dbms", {"dbms", "database", "sql", "normalization", "join", "joins", "indexing", "transaction"}),
        ("ml", {"machine learning", "ml", "deep learning", "ai", "artificial intelligence"}),
        ("nlp", {"nlp", "natural language processing", "tokenization", "parsing", "language model"}),
        ("dsa", {"data structure", "dsa", "algorithm", "algorithms", "graph", "graphs", "tree", "topological sort", "kahn", "bfs", "dfs"}),
        ("compiler", {"compiler", "compiler design", "lexical analysis", "syntax analysis", "semantic analysis"}),
        ("network", {"network", "networking", "osi", "tcp", "ip", "routing"}),
        ("math", {"math", "mathematics", "probability", "statistics", "calculus", "slope", "derivative", "integral"}),
        ("general", {"trojan", "malware", "virus", "worm"}),
    ]
    for domain, phrases in checks:
        if any(_contains_phrase(query, phrase) for phrase in phrases):
            return domain

    subject_text = " ".join(subject.name for subject in subjects)
    if _contains_phrase(subject_text, "database") or _contains_phrase(subject_text, "dbms"):
        if any(word in query for word in ["normalization", "join", "sql", "index", "schema"]):
            return "dbms"
    return "general"


def _format_focus_title(focus: str) -> str:
    if not focus:
        return "This topic"
    words = [word.upper() if len(word) <= 4 else word.capitalize() for word in focus.split()]
    return " ".join(words)


def _build_dynamic_educational_answer(
    message: str,
    subjects: list[Subject],
) -> tuple[str, list[str], list[str]] | None:
    intent = _question_intent(message)
    if intent not in {"explain", "types", "compare", "how", "exam_plan", "code"}:
        return None

    focus = _extract_focus_phrase(message)
    query = _normalize_educational_query(message)
    domain_key = _infer_domain_key(message, subjects)
    frame = DOMAIN_FRAMES.get(domain_key, DOMAIN_FRAMES["general"])
    label = str(frame["label"])
    core = list(frame["core"])
    applications = list(frame["applications"])
    study_tip = str(frame["study_tip"])
    suggestions = list(frame["suggestions"])
    focus_title = _format_focus_title(focus)

    if (
        ("binary tree" in query and "binary search tree" in query)
        or ("binary trees" in query and "binary search trees" in query)
        or ("bst" in query and "binary tree" in query)
    ):
        return (
            "A binary tree is a tree data structure in which each node has at most two children, called the left child and right child.\n"
            "A binary search tree (BST) is a special type of binary tree that follows an ordering rule:\n"
            "- all values in the left subtree are smaller than the root\n"
            "- all values in the right subtree are greater than the root\n\n"
            "Difference between them:\n"
            "- Binary tree: no fixed ordering of node values is required\n"
            "- BST: nodes are arranged in sorted order based on the BST property\n"
            "- Binary tree is mainly used for hierarchical representation\n"
            "- BST is used for efficient searching, insertion, and deletion\n\n"
            "In short: every BST is a binary tree, but every binary tree is not a BST.",
            ["general_education"],
            ["What is a binary tree?", "What is a binary search tree?", "Difference between BFS and DFS"],
        )

    if ("bfs" in focus and "dfs" in focus) or (" bfs " in f" {query} " and " dfs " in f" {query} "):
        return (
            "BFS and DFS are both graph traversal algorithms, but they explore the graph differently.\n"
            "- BFS explores level by level and usually uses a queue\n"
            "- DFS goes as deep as possible first and usually uses recursion or a stack\n"
            "- BFS is preferred for shortest path in an unweighted graph\n"
            "- DFS is preferred for backtracking, cycle checks, and topological-sort style traversals\n"
            "- Both have time complexity O(V + E)\n\n"
            "In short: BFS is breadth-wise exploration, while DFS is depth-wise exploration.",
            ["general_education"],
            ["Explain BFS", "Explain DFS", "Topological sort using DFS"],
        )

    if focus in {"bfs", "breadth first search"} or " bfs " in f" {focus} ":
        return (
            "BFS stands for Breadth-First Search. It is a graph traversal algorithm that visits nodes level by level.\n"
            "Main idea:\n"
            "- start from a source node\n"
            "- visit all its immediate neighbors first\n"
            "- then visit the neighbors of those nodes\n"
            "- use a queue to maintain the visiting order\n\n"
            "BFS is used to find the shortest path in an unweighted graph, to traverse trees level by level, and to explore connected components.\n\n"
            "Time complexity is O(V + E).",
            ["general_education"],
            ["Explain DFS", "Difference between BFS and DFS", "What is a graph traversal?"],
        )

    if focus in {"dfs", "depth first search"} or " dfs " in f" {focus} ":
        return (
            "DFS stands for Depth-First Search. It is a graph traversal algorithm that explores one path as deeply as possible before backtracking.\n"
            "Main idea:\n"
            "- start from a source node\n"
            "- visit one unvisited neighbor and keep going deeper\n"
            "- when no unvisited neighbor remains, backtrack\n"
            "- it is usually implemented using recursion or a stack\n\n"
            "DFS is used in cycle detection, topological sorting, connected components, and maze-like exploration.\n\n"
            "Time complexity is O(V + E).",
            ["general_education"],
            ["Explain BFS", "Difference between BFS and DFS", "Topological sort using DFS"],
        )

    if "kahn" in focus or "topological sort" in focus:
        return (
            "Kahn's algorithm is a method for topological sorting of a directed acyclic graph (DAG).\n"
            "Main idea:\n"
            "- first compute the in-degree of every vertex\n"
            "- put all vertices with in-degree 0 into a queue\n"
            "- remove one vertex from the queue, add it to the answer, and reduce the in-degree of its outgoing neighbors\n"
            "- if any neighbor becomes 0, push it into the queue\n"
            "- continue until the queue becomes empty\n\n"
            "If all vertices are processed, the produced order is a valid topological order. If some vertices remain, the graph contains a cycle.\n\n"
            "Time complexity is O(V + E). It is used in prerequisite ordering, dependency resolution, and scheduling problems.",
            ["general_education"],
            ["What is topological sort?", "Topological sort using DFS", "What is a DAG?"],
        )

    if "trojan" in focus:
        return (
            "A Trojan is a type of malicious software that looks legitimate or harmless but performs harmful actions after the user runs it.\n"
            "Main points:\n"
            "- it usually hides inside a normal-looking file or program\n"
            "- unlike a virus, it does not mainly spread by attaching itself to other files\n"
            "- it may steal data, open a backdoor, spy on the user, or damage the system\n\n"
            "Examples of impact include password theft, remote access, and data leakage. Prevention includes avoiding unknown downloads, scanning files, and keeping antivirus protection updated.",
            ["general_education"],
            ["Difference between virus and trojan", "What is malware?", "What is a worm in cybersecurity?"],
        )

    if "topological sort using dfs" in query or ("topological sort" in query and "dfs" in query):
        return (
            "Topological sort using DFS works by visiting nodes depth-first and pushing each node to a stack only after all of its outgoing neighbors are processed.\n"
            "Steps:\n"
            "- mark the current node as visited\n"
            "- recursively visit all unvisited adjacent nodes\n"
            "- after visiting all neighbors, push the current node onto a stack\n"
            "- after DFS finishes for all nodes, reverse the stack or pop from it to get the topological order\n\n"
            "This works only for a directed acyclic graph (DAG). If the graph contains a cycle, topological sorting is not valid.\n\n"
            "Time complexity is O(V + E).",
            ["general_education"],
            ["Topological sort code Java", "Explain Kahn's algorithm", "What is a DAG?"],
        )

    if intent == "code":
        wants_java = " java " in f" {query} "
        wants_python = " python " in f" {query} "
        wants_cpp = " c++ " in f" {query} " or " cpp " in f" {query} "

        if "dfs" in query:
            if wants_java:
                return (
                    "Here is Java code for DFS traversal of a graph:\n\n"
                    "```java\n"
                    "import java.util.*;\n\n"
                    "public class DFSExample {\n"
                    "    static void dfs(int node, List<List<Integer>> graph, boolean[] visited) {\n"
                    "        visited[node] = true;\n"
                    "        System.out.print(node + \" \");\n"
                    "        for (int next : graph.get(node)) {\n"
                    "            if (!visited[next]) {\n"
                    "                dfs(next, graph, visited);\n"
                    "            }\n"
                    "        }\n"
                    "    }\n"
                    "}\n"
                    "```",
                    ["general_education"],
                    ["Explain DFS", "Difference between BFS and DFS", "Topological sort using DFS"],
                )
            if wants_python or not wants_cpp:
                return (
                    "Here is Python code for DFS traversal of a graph:\n\n"
                    "```python\n"
                    "def dfs(node, graph, visited):\n"
                    "    visited.add(node)\n"
                    "    print(node, end=' ')\n"
                    "    for nxt in graph[node]:\n"
                    "        if nxt not in visited:\n"
                    "            dfs(nxt, graph, visited)\n"
                    "\n"
                    "graph = {\n"
                    "    0: [1, 2],\n"
                    "    1: [3],\n"
                    "    2: [4],\n"
                    "    3: [],\n"
                    "    4: []\n"
                    "}\n"
                    "dfs(0, graph, set())\n"
                    "```\n\n"
                    "This uses recursion and a visited set to avoid revisiting nodes.",
                    ["general_education"],
                    ["Explain DFS", "Difference between BFS and DFS", "Topological sort using DFS"],
                )
            return (
                "Here is C++ code for DFS traversal of a graph:\n\n"
                "```cpp\n"
                "#include <bits/stdc++.h>\n"
                "using namespace std;\n\n"
                "void dfs(int node, vector<vector<int>>& graph, vector<bool>& visited) {\n"
                "    visited[node] = true;\n"
                "    cout << node << \" \";\n"
                "    for (int nxt : graph[node]) {\n"
                "        if (!visited[nxt]) dfs(nxt, graph, visited);\n"
                "    }\n"
                "}\n"
                "```",
                ["general_education"],
                ["Explain DFS", "Difference between BFS and DFS", "Topological sort using DFS"],
            )

        if "binary search tree" in query or " bst " in f" {query} ":
            if wants_java:
                return (
                    "Here is Java code to create a Binary Search Tree and insert nodes:\n\n"
                    "```java\n"
                    "class Node {\n"
                    "    int data;\n"
                    "    Node left, right;\n"
                    "    Node(int data) { this.data = data; }\n"
                    "}\n\n"
                    "public class BST {\n"
                    "    Node root;\n\n"
                    "    Node insert(Node root, int key) {\n"
                    "        if (root == null) return new Node(key);\n"
                    "        if (key < root.data) root.left = insert(root.left, key);\n"
                    "        else if (key > root.data) root.right = insert(root.right, key);\n"
                    "        return root;\n"
                    "    }\n"
                    "}\n"
                    "```",
                    ["general_education"],
                    ["What is a binary search tree?", "Binary tree vs BST", "BST traversal code"],
                )
            if wants_cpp:
                return (
                    "Here is C++ code to create a Binary Search Tree and insert nodes:\n\n"
                    "```cpp\n"
                    "#include <bits/stdc++.h>\n"
                    "using namespace std;\n\n"
                    "struct Node {\n"
                    "    int data;\n"
                    "    Node *left, *right;\n"
                    "    Node(int val) : data(val), left(nullptr), right(nullptr) {}\n"
                    "};\n\n"
                    "Node* insert(Node* root, int key) {\n"
                    "    if (!root) return new Node(key);\n"
                    "    if (key < root->data) root->left = insert(root->left, key);\n"
                    "    else if (key > root->data) root->right = insert(root->right, key);\n"
                    "    return root;\n"
                    "}\n"
                    "```",
                    ["general_education"],
                    ["What is a binary search tree?", "Binary tree vs BST", "BST traversal code"],
                )
            return (
                "Here is Python code to create a Binary Search Tree and insert nodes:\n\n"
                "```python\n"
                "class Node:\n"
                "    def __init__(self, data):\n"
                "        self.data = data\n"
                "        self.left = None\n"
                "        self.right = None\n"
                "\n"
                "def insert(root, key):\n"
                "    if root is None:\n"
                "        return Node(key)\n"
                "    if key < root.data:\n"
                "        root.left = insert(root.left, key)\n"
                "    elif key > root.data:\n"
                "        root.right = insert(root.right, key)\n"
                "    return root\n"
                "```",
                ["general_education"],
                ["What is a binary search tree?", "Binary tree vs BST", "BST traversal code"],
            )

        if "topological sort" in focus or "kahn" in focus:
            if wants_java:
                return (
                    "Here is a Java implementation of topological sort using Kahn's algorithm:\n\n"
                    "```java\n"
                    "import java.util.*;\n\n"
                    "public class TopologicalSort {\n"
                    "    public static List<Integer> topologicalSort(int n, List<int[]> edges) {\n"
                    "        List<List<Integer>> graph = new ArrayList<>();\n"
                    "        int[] indegree = new int[n];\n"
                    "        for (int i = 0; i < n; i++) graph.add(new ArrayList<>());\n"
                    "        for (int[] edge : edges) {\n"
                    "            graph.get(edge[0]).add(edge[1]);\n"
                    "            indegree[edge[1]]++;\n"
                    "        }\n"
                    "        Queue<Integer> queue = new LinkedList<>();\n"
                    "        for (int i = 0; i < n; i++) {\n"
                    "            if (indegree[i] == 0) queue.offer(i);\n"
                    "        }\n"
                    "        List<Integer> order = new ArrayList<>();\n"
                    "        while (!queue.isEmpty()) {\n"
                    "            int node = queue.poll();\n"
                    "            order.add(node);\n"
                    "            for (int next : graph.get(node)) {\n"
                    "                indegree[next]--;\n"
                    "                if (indegree[next] == 0) queue.offer(next);\n"
                    "            }\n"
                    "        }\n"
                    "        return order.size() == n ? order : new ArrayList<>();\n"
                    "    }\n"
                    "}\n"
                    "```\n\n"
                    "Use an empty result to indicate that the graph contains a cycle.",
                    ["general_education"],
                    ["Explain Kahn's algorithm", "Topological sort using DFS", "Topological sort code in Python"],
                )
            if wants_python:
                return (
                    "Here is a Python implementation of topological sort using Kahn's algorithm:\n\n"
                    "```python\n"
                    "from collections import deque\n\n"
                    "def topological_sort(n, edges):\n"
                    "    graph = [[] for _ in range(n)]\n"
                    "    indegree = [0] * n\n"
                    "    for u, v in edges:\n"
                    "        graph[u].append(v)\n"
                    "        indegree[v] += 1\n"
                    "    queue = deque(i for i in range(n) if indegree[i] == 0)\n"
                    "    order = []\n"
                    "    while queue:\n"
                    "        node = queue.popleft()\n"
                    "        order.append(node)\n"
                    "        for nxt in graph[node]:\n"
                    "            indegree[nxt] -= 1\n"
                    "            if indegree[nxt] == 0:\n"
                    "                queue.append(nxt)\n"
                    "    return order if len(order) == n else []\n"
                    "```\n\n"
                    "If the returned list is shorter than `n`, the graph has a cycle.",
                    ["general_education"],
                    ["Explain Kahn's algorithm", "Topological sort code in Java", "What is a DAG?"],
                )
            if wants_cpp:
                return (
                    "Here is a C++ implementation of topological sort using Kahn's algorithm:\n\n"
                    "```cpp\n"
                    "#include <bits/stdc++.h>\n"
                    "using namespace std;\n\n"
                    "vector<int> topologicalSort(int n, vector<pair<int, int>> edges) {\n"
                    "    vector<vector<int>> graph(n);\n"
                    "    vector<int> indegree(n, 0), order;\n"
                    "    for (auto [u, v] : edges) {\n"
                    "        graph[u].push_back(v);\n"
                    "        indegree[v]++;\n"
                    "    }\n"
                    "    queue<int> q;\n"
                    "    for (int i = 0; i < n; i++) if (indegree[i] == 0) q.push(i);\n"
                    "    while (!q.empty()) {\n"
                    "        int node = q.front(); q.pop();\n"
                    "        order.push_back(node);\n"
                    "        for (int nxt : graph[node]) {\n"
                    "            if (--indegree[nxt] == 0) q.push(nxt);\n"
                    "        }\n"
                    "    }\n"
                    "    return order.size() == n ? order : vector<int>{};\n"
                    "}\n"
                    "```",
                    ["general_education"],
                    ["Explain Kahn's algorithm", "Topological sort code in Java", "Topological sort using DFS"],
                )
            return (
                "Here is pseudocode for topological sort using Kahn's algorithm:\n\n"
                "```text\n"
                "compute indegree of every vertex\n"
                "push all vertices with indegree 0 into a queue\n"
                "while queue is not empty:\n"
                "    remove front vertex\n"
                "    add it to answer\n"
                "    for each outgoing neighbor:\n"
                "        decrease indegree by 1\n"
                "        if indegree becomes 0, push it into queue\n"
                "if answer does not contain all vertices, graph has a cycle\n"
                "```\n\n"
                "Ask for Java, Python, or C++ if you want full code.",
                ["general_education"],
                ["Topological sort code Java", "Topological sort code Python", "Explain Kahn's algorithm"],
            )

    if intent == "exam_plan":
        return (
            "Use a 7-day exam plan:\n"
            "- Day 1: list all units, mark weak and strong topics, and collect notes, formulas, and past questions\n"
            "- Days 2 to 5: revise the most important and weakest units first using active recall and short tests\n"
            "- Day 6: solve mixed questions or a past paper under time limits and review mistakes\n"
            "- Day 7: do light revision only, review key definitions, formulas, and mistakes, then rest properly\n\n"
            "Daily pattern:\n"
            "- 45 to 60 minutes study\n"
            "- 10 minute break\n"
            "- end each block with self-recall without notes",
            ["guidance"],
            ["Make me a 7-day revision plan", "How do I revise Unit 1?", "How should I use quizzes for revision?"],
        )

    if intent == "types":
        return (
            f"{focus_title} can be explained by classifying it into its main categories in {label}.\n"
            f"Focus on these points:\n"
            f"- define what {focus} means in one line\n"
            f"- list the main categories or forms\n"
            f"- explain how each category differs in purpose or behavior\n"
            f"- add one example or application for each type\n\n"
            f"For this domain, useful anchors are: {', '.join(core[:4])}.",
            ["general_education"],
            suggestions,
        )

    if intent == "compare":
        return (
            f"To compare {focus_title}, structure the answer in {label} like this:\n"
            f"- define both terms first\n"
            f"- compare them by purpose, working, output, and use case\n"
            f"- include one example for each side\n"
            f"- finish with when each one is preferred\n\n"
            f"Key comparison angles in this domain are: {', '.join(core[:4])}.",
            ["general_education"],
            suggestions,
        )

    if intent == "how":
        return (
            f"For a 'how' question about {focus_title}, explain the process step by step.\n"
            f"- start with the goal or purpose\n"
            f"- describe the main stages or workflow\n"
            f"- mention the important components involved\n"
            f"- end with where it is applied or why it matters\n\n"
            f"In {label}, common anchors are: {', '.join(core[:4])}.",
            ["general_education"],
            suggestions,
        )

    return (
        f"{focus_title} is an important concept in {label}.\n"
        f"To explain it clearly, cover these points:\n"
        f"- what it means and why it is used\n"
        f"- the main ideas around it: {', '.join(core[:4])}\n"
        f"- where it is applied: {', '.join(applications[:3])}\n"
        f"- one simple example or use case\n\n"
        f"Exam tip: {study_tip}.",
        ["general_education"],
        suggestions,
    )


def _enhance_fallback_answer(
    message: str,
    answer: str,
    sources: list[str],
    suggestions: list[str],
) -> tuple[str, list[str], list[str]]:
    return answer, sources, suggestions


def _is_general_educational_query(message: str) -> bool:
    query = _normalize_educational_query(message)
    if not query:
        return False

    educational_terms = {
        "ai",
        "ml",
        "machine learning",
        "deep learning",
        "dbms",
        "sql",
        "database",
        "normalization",
        "join",
        "joins",
        "indexing",
        "python",
        "java",
        "c programming",
        "programming",
        "algorithm",
        "algorithms",
        "data structure",
        "dsa",
        "operating system",
        "os",
        "linux",
        "network",
        "networking",
        "compiler",
        "cloud",
        "cybersecurity",
        "blockchain",
        "statistics",
        "probability",
        "calculus",
        "economics",
        "nlp",
        "iot",
        "information retrieval",
        "data analytics",
        "data mining",
        "oops",
        "oop",
        "study",
        "exam",
        "revision",
        "quiz",
        "education",
        "geography",
        "history",
        "political science",
        "polity",
        "civics",
        "government",
        "minister",
        "finance minister",
        "prime minister",
        "president",
        "constitution",
        "independence",
        "freedom struggle",
    }
    intent_terms = {
        "explain",
        "what is",
        "what are",
        "define",
        "difference between",
        "compare",
        "how does",
        "how do",
        "types of",
        "why",
        "when",
        "example",
        "examples",
        "advantages",
        "disadvantages",
    }
    return any(_contains_phrase(query, term) for term in educational_terms | intent_terms)


def _is_study_data_query(message: str) -> bool:
    query = _normalize_educational_query(message)
    if not query:
        return False
    study_data_terms = {
        "my subject",
        "my subjects",
        "list my subjects",
        "show my subjects",
        "show topics",
        "list topics",
        "my topics",
        "my schedule",
        "my timetable",
        "today",
        "next session",
        "next sessions",
        "what should i study next",
        "revise this week",
        "my unit",
    }
    return any(_contains_phrase(query, term) for term in study_data_terms)


def _llm_unavailable_answer() -> tuple[str, list[str], list[str]]:
    return (
        "Educational AI is not available right now. Configure a working Groq API key in your `.env` file to get full GPT-style educational answers.",
        ["llm_required"],
        ["List my subjects", "Show my schedule", "What should I study next?"],
    )


def _llm_temporarily_unavailable_answer() -> tuple[str, list[str], list[str]]:
    return (
        "Groq is temporarily unavailable right now, so I could not generate an LLM answer for this educational question. Please try again in a moment.",
        ["groq_unavailable"],
        ["Retry this question", "Show my schedule", "List my subjects"],
    )


def _needs_educational_clarification(message: str) -> bool:
    query = _normalize_educational_query(message)
    if not query:
        return False

    short_query = len(query.split()) <= 4
    broad_but_ambiguous = {
        "graph",
        "graphs",
        "tree",
        "trees",
        "stack",
        "stacks",
        "queue",
        "queues",
        "model",
        "models",
        "process",
        "procedure",
        "formula",
        "formulas",
        "theory",
        "system",
        "systems",
    }
    return short_query and any(_contains_phrase(query, term) for term in broad_but_ambiguous)


def _is_non_educational_query(message: str) -> bool:
    query = _normalize_educational_query(message)
    if not query:
        return False

    non_educational_terms = {
        "trip",
        "trips",
        "travel",
        "tour",
        "tourism",
        "vacation",
        "holiday",
        "hotel",
        "flight",
        "restaurant",
        "food",
        "cooking",
        "recipe",
        "recipes",
        "movie",
        "song",
        "lyrics",
        "celebrity",
        "actor",
        "actress",
        "cricket score",
        "match score",
        "world cup",
        "t20 world cup",
        "who won",
        "ipl",
        "football match",
        "weather",
        "bitcoin price",
        "stock price",
        "shopping",
        "shopping list",
        "buy",
        "amazon",
        "flipkart",
        "motivation",
    }
    return any(_contains_phrase(query, term) for term in non_educational_terms)


def _is_compliment(message: str) -> bool:
    """Detect short appreciative/compliment messages."""
    query = f" {_normalize_educational_query(message)} "
    compliment_terms = [
        "good bot",
        "great",
        "awesome",
        "nice",
        "well done",
        "thanks",
        "thank you",
        "appreciate",
        "helpful",
        "so good",
        "good job",
        "cool",
    ]
    return any(term in query for term in compliment_terms)


def _non_educational_redirect() -> tuple[str, list[str], list[str]]:
    return (
        "I am restricted to educational help only. Ask about concepts, formulas, programming, exam preparation, study methods, or your syllabus and timetable.",
        ["education_only"],
        ["Explain graphs", "What is data mining?", "How do I revise Unit 1?"],
    )


def _educational_clarification_redirect() -> tuple[str, list[str], list[str]]:
    return (
        "Your question looks educational, but it is too broad or ambiguous. Ask with a little more context, such as the subject or exact concept you want.",
        ["education_clarification"],
        ["Explain graphs in data structures", "Explain trees in DSA", "Explain slope formula in maths"],
    )


def _general_educational_answer(message: str) -> tuple[str, list[str], list[str]] | None:
    query = _normalize_educational_query(message)
    expanded_query = f" {query} "
    expanded_query = expanded_query.replace(" da ", " data analytics ")
    if _is_compliment(message):
        return (
            "Thanks! Ask any educational question or tell me a subject/unit to focus on.",
            ["general_education"],
            ["What should I study next?", "Show topics in a subject", "How do I revise this unit?"],
        )
    if any(greeting in expanded_query for greeting in [" hi ", " hello ", " hey "]):
        return (
            "Ask any educational question, concept doubt, exam-prep question, or study-method question. "
            "I can explain topics across programming, CS, analytics, economics, math, and more.",
            ["general_education"],
            ["Explain machine learning types", "What is normalization in DBMS?", "How do I prepare for exams?"],
        )

    explain_like = any(
        phrase in query
        for phrase in [
            "explain",
            "what is",
            "what are",
            "types of",
            "define",
            "difference between",
            "compare",
            "how does",
            "how do",
            "why",
            "advantages",
            "disadvantages",
            "example",
            "examples",
        ]
    )

    dynamic_answer = _build_dynamic_educational_answer(message, [])
    if dynamic_answer is not None:
        return dynamic_answer

    if not explain_like and not _is_general_educational_query(message):
        return None

    if "deep learning" in expanded_query:
        return (
            "Deep learning is a subset of machine learning that uses multi-layer neural networks to learn complex patterns from large amounts of data. "
            "It is widely used in image recognition, speech processing, NLP, and recommendation systems.\n"
            "- Uses neural networks with many layers\n"
            "- Learns features automatically from data\n"
            "- Needs more data and computing power than traditional ML",
            ["general_education"],
            ["AI vs ML vs deep learning", "What is a neural network?", "Applications of deep learning"],
        )

    if "linux" in expanded_query:
        return (
            "Linux is an open-source operating system based on Unix. It is widely used in servers, cloud systems, embedded devices, cybersecurity, and development environments.\n"
            "- Multiuser and multitasking\n"
            "- Secure and stable\n"
            "- Uses a command-line shell as well as graphical environments\n"
            "- Popular distributions include Ubuntu, Fedora, Debian, and Arch Linux\n\n"
            "In short: Linux is a powerful, flexible OS widely used in technical and production systems.",
            ["general_education"],
            ["What is the Linux kernel?", "Common Linux commands", "Linux vs Windows"],
        )

    if "spring boot" in expanded_query:
        return (
            "Spring Boot is a Java framework built on top of Spring that makes it easier to create production-ready applications quickly.\n"
            "- Provides auto-configuration\n"
            "- Includes embedded servers like Tomcat\n"
            "- Reduces boilerplate setup\n"
            "- Commonly used for REST APIs and microservices\n\n"
            "In short: Spring Boot simplifies Spring application development.",
            ["general_education"],
            ["What is dependency injection?", "Explain REST API in Spring Boot", "Spring vs Spring Boot"],
        )

    if "iot architecture" in expanded_query:
        return (
            "IoT architecture explains how IoT components are organized to collect, transmit, process, and use data.\n"
            "- Perception layer: sensors and devices collect data\n"
            "- Network layer: transfers data using communication protocols\n"
            "- Processing layer: stores and analyzes data, often in cloud or edge systems\n"
            "- Application layer: delivers user-facing services such as smart health, agriculture, or monitoring\n\n"
            "Some models also include a business layer for management and analytics.",
            ["general_education"],
            ["Explain IoT layers", "Sensors vs actuators", "IoT vs M2M"],
        )

    if "compiler phases" in expanded_query or "phases of compiler" in expanded_query:
        return (
            "The main phases of a compiler are:\n"
            "- Lexical analysis: converts characters into tokens\n"
            "- Syntax analysis: checks grammatical structure using parsing\n"
            "- Semantic analysis: checks meaning, types, and declarations\n"
            "- Intermediate code generation: creates a middle-level representation\n"
            "- Code optimization: improves efficiency\n"
            "- Code generation: produces target machine code\n\n"
            "Symbol table management and error handling support all phases.",
            ["general_education"],
            ["What is lexical analysis?", "What is parsing?", "Explain semantic analysis"],
        )

    if "semiconductor" in expanded_query or "semiconductors" in expanded_query:
        return (
            "A semiconductor is a material whose electrical conductivity lies between that of a conductor and an insulator. "
            "Its conductivity can be controlled by temperature, light, voltage, or impurities.\n"
            "- Common examples: silicon and germanium\n"
            "- Two main types: intrinsic and extrinsic semiconductors\n"
            "- Extrinsic semiconductors are of two types: n-type and p-type\n"
            "- Semiconductors are used in diodes, transistors, ICs, and solar cells",
            ["general_education"],
            ["What is intrinsic vs extrinsic semiconductor?", "Explain p-type and n-type semiconductor", "Applications of semiconductors"],
        )

    if "slope formula" in expanded_query or ("formula" in expanded_query and "slope" in expanded_query):
        return (
            "The slope of a line through two points (x1, y1) and (x2, y2) is:\n"
            "m = (y2 - y1) / (x2 - x1)\n\n"
            "It tells how much y changes for a change in x.\n"
            "- Positive slope: line rises\n"
            "- Negative slope: line falls\n"
            "- Zero slope: horizontal line\n"
            "- Undefined slope: vertical line",
            ["general_education"],
            ["What is slope in coordinate geometry?", "Equation of a straight line", "Point-slope form of a line"],
        )

    if "straight line" in expanded_query or "equation of line" in expanded_query:
        return (
            "Common forms of the equation of a straight line are:\n"
            "- Slope-intercept form: y = mx + c\n"
            "- Point-slope form: y - y1 = m(x - x1)\n"
            "- Two-point form: y - y1 = ((y2 - y1)/(x2 - x1)) (x - x1)\n"
            "- General form: Ax + By + C = 0",
            ["general_education"],
            ["Explain slope formula", "What is point-slope form?", "How to find equation of a line from two points"],
        )

    if "normalization" in expanded_query:
        return (
            "Normalization in DBMS is the process of organizing data to reduce redundancy and improve consistency.\n"
            "- 1NF: remove repeating groups and keep values atomic\n"
            "- 2NF: remove partial dependency on part of a composite key\n"
            "- 3NF: remove transitive dependency\n"
            "- BCNF: every determinant should be a candidate key\n\n"
            "Why it is used:\n"
            "- reduces duplicate data\n"
            "- avoids update, insertion, and deletion anomalies\n"
            "- improves data integrity\n\n"
            "In exams, write the definition, objective, and one line for each normal form, then give a tiny table example.",
            ["general_education"],
            ["Explain 1NF 2NF 3NF", "What is BCNF?", "What are joins in SQL?"],
        )

    if "topological sort" in expanded_query or "topological ordering" in expanded_query:
        return (
            "Topological sorting is a linear ordering of the vertices of a directed acyclic graph (DAG) such that if there is an edge from u to v, then u appears before v in the order.\n"
            "- It is defined only for DAGs\n"
            "- It is used in task scheduling, prerequisite planning, and dependency resolution\n"
            "- Common methods are Kahn's algorithm and DFS-based ordering\n\n"
            "Example: if A must happen before B, then A will appear before B in the topological order.",
            ["general_education"],
            ["Explain Kahn's algorithm", "What is a DAG?", "Topological sort using DFS"],
        )

    if " join " in expanded_query or " joins " in expanded_query:
        return (
            "Joins in SQL are used to combine rows from two or more tables based on a related column.\n"
            "- INNER JOIN: returns matching rows from both tables\n"
            "- LEFT JOIN: all rows from left table and matched rows from right\n"
            "- RIGHT JOIN: all rows from right table and matched rows from left\n"
            "- FULL OUTER JOIN: all matched and unmatched rows from both tables\n"
            "- SELF JOIN: joins a table with itself\n\n"
            "For exams, define join, then explain each type with a simple example.",
            ["general_education"],
            ["Explain INNER JOIN vs LEFT JOIN", "Give SQL join example", "Explain normalization"],
        )

    if "indexing" in expanded_query and ("dbms" in expanded_query or "database" in expanded_query or "sql" in expanded_query):
        return (
            "Indexing in DBMS is a technique used to speed up data retrieval by creating a separate structure that helps the database find rows faster.\n"
            "- Improves search performance\n"
            "- Common types: primary index, secondary index, clustered index, non-clustered index\n"
            "- Uses extra storage and can slow inserts or updates\n\n"
            "In short: indexing improves read speed but adds maintenance cost.",
            ["general_education"],
            ["What is normalization?", "What are joins in SQL?", "Clustered vs non-clustered index"],
        )

    if "types of data analytics" in expanded_query or "data analytics types" in expanded_query:
        return (
            "The main types of Data Analytics are:\n"
            "- Descriptive analytics: what happened\n"
            "- Diagnostic analytics: why it happened\n"
            "- Predictive analytics: what is likely to happen\n"
            "- Prescriptive analytics: what should be done\n\n"
            "A short exam answer is: descriptive, diagnostic, predictive, and prescriptive analytics.",
            ["general_education"],
            ["Explain descriptive vs predictive analytics", "Applications of data analytics", "What is machine learning?"],
        )

    if "types of ml" in expanded_query or "types of machine learning" in expanded_query:
        return (
            "The main types of machine learning are:\n"
            "- Supervised learning: learns from labeled examples\n"
            "- Unsupervised learning: finds hidden patterns in unlabeled data\n"
            "- Reinforcement learning: learns actions using rewards and penalties\n\n"
            "You can also mention semi-supervised learning as a hybrid approach in some answers.",
            ["general_education"],
            ["Explain supervised learning", "Difference between supervised and unsupervised learning", "What is reinforcement learning?"],
        )

    if "types of ai" in expanded_query:
        return (
            "AI is commonly classified in two ways.\n"
            "- By capability: Narrow AI, General AI, Super AI\n"
            "- By functionality: Reactive machines, limited memory, theory of mind, self-aware systems\n\n"
            "In most exams, Narrow AI vs General AI is the most useful distinction.",
            ["general_education"],
            ["AI vs ML", "Applications of AI", "What is deep learning?"],
        )

    if "what is data analytics" in expanded_query or "define data analytics" in expanded_query:
        return (
            "Data analytics is the process of collecting, cleaning, transforming, and analyzing data to discover useful information and support decision-making. "
            "It combines statistics, data processing, visualization, and modeling.",
            ["general_education"],
            ["Types of data analytics", "Data analytics vs data mining", "Applications of data analytics"],
        )

    if "what is machine learning" in expanded_query or "define machine learning" in expanded_query:
        return (
            "Machine learning is a branch of AI in which systems learn patterns from data and improve predictions or decisions without being explicitly programmed for every case.",
            ["general_education"],
            ["Types of ML", "AI vs ML", "What is deep learning?"],
        )

    if "difference between" in expanded_query or "compare" in expanded_query:
        if _contains_phrase(query, "ai") and _contains_phrase(query, "ml"):
            return (
                "AI is the broader field of creating intelligent systems, while ML is a subset of AI that learns patterns from data.\n"
                "- AI aims to simulate intelligent behavior\n"
                "- ML focuses on learning from examples\n"
                "- All ML is AI, but not all AI is ML\n\n"
                "In short: AI is the umbrella term, ML is one approach inside AI.",
                ["general_education"],
                ["Types of AI", "Types of ML", "What is deep learning?"],
            )
        return (
            "For a good comparison answer, write:\n"
            "- definition of both terms\n"
            "- 3 to 5 direct differences\n"
            "- one example for each\n"
            "- a short conclusion about where each is used\n\n"
            "Ask again with the exact two topics if you want a specific comparison.",
            ["general_education"],
            ["Difference between AI and ML", "Compare DBMS and file system", "Difference between TCP and UDP"],
        )

    concept_map: list[tuple[set[str], str, list[str]]] = [
        (
            {"ml", "machine learning"},
            "Machine learning is usually grouped into 3 main types:\n"
            "- Supervised learning: learns from labeled data to predict outputs. Examples: classification and regression.\n"
            "- Unsupervised learning: finds patterns in unlabeled data. Examples: clustering and dimensionality reduction.\n"
            "- Reinforcement learning: an agent learns by trial and error using rewards and penalties.\n\n"
            "A simple exam answer is: supervised uses labeled data, unsupervised uses unlabeled data, and reinforcement learns from feedback.\n\n"
            "You can also mention examples such as spam detection for supervised learning, customer segmentation for unsupervised learning, and game playing or robotics for reinforcement learning.",
            ["Explain supervised vs unsupervised learning", "Give ML examples", "What is reinforcement learning?"],
        ),
        (
            {"dbms", "normalization", "sql", "database"},
            "DBMS is software used to store, manage, and retrieve structured data efficiently.\n"
            "Its main functions are data storage, security, consistency, concurrency control, backup, and recovery.\n"
            "Key topics usually include normalization, SQL queries, joins, indexing, transactions, and concurrency control.",
            ["Explain normalization", "What are joins in SQL?", "What is indexing in DBMS?"],
        ),
        (
            {"oops", "oop", "object oriented"},
            "The main OOP concepts are:\n"
            "- Encapsulation\n- Abstraction\n- Inheritance\n- Polymorphism\n\n"
            "A good answer also explains each with one short example.",
            ["Explain encapsulation", "Difference between inheritance and polymorphism", "OOP with examples"],
        ),
        (
            {"os", "operating system", "deadlock", "scheduling"},
            "Operating system questions often focus on process scheduling, deadlocks, memory management, paging, synchronization, and file systems.",
            ["What is deadlock?", "Explain CPU scheduling", "What is paging?"],
        ),
        (
            {"linux", "unix"},
            "Linux is an open-source operating system widely used in servers, cloud computing, development, and embedded systems. Key topics include the kernel, shell, file system, permissions, processes, and commands.",
            ["What is Linux?", "Common Linux commands", "Linux vs Unix"],
        ),
        (
            {"dsa", "data structure", "algorithm", "algorithms"},
            "For data structures and algorithms, explain the idea first, then give steps, time complexity, space complexity, and one example.",
            ["Explain time complexity", "What is a stack?", "Difference between BFS and DFS"],
        ),
        (
            {"python"},
            "Python is a high-level interpreted language known for readable syntax. Important basics are variables, data types, loops, functions, lists, dictionaries, file handling, and OOP.",
            ["Explain Python lists vs tuples", "What are Python dictionaries?", "Explain functions in Python"],
        ),
        (
            {"nlp", "natural language processing"},
            "Natural Language Processing (NLP) is a branch of AI that helps computers understand, interpret, and generate human language.\n"
            "Common tasks in NLP include tokenization, parsing, part-of-speech tagging, named entity recognition, sentiment analysis, machine translation, question answering, and text summarization.\n"
            "It is used in chatbots, search engines, voice assistants, spell checkers, and translation systems.",
            ["What is tokenization?", "Explain language models", "What is parsing in NLP?"],
        ),
        (
            {"iot", "internet of things"},
            "IoT means connecting physical devices to the internet so they can sense, exchange, and act on data. Core ideas are sensors, actuators, communication, edge devices, and analytics.",
            ["Explain sensors and actuators", "What is IoT architecture?", "IoT vs M2M"],
        ),
        (
            {"information retrieval", "ir"},
            "Information Retrieval is about finding relevant information from large collections of documents. Core topics include indexing, ranking, search models, clustering, and multimedia retrieval.",
            ["What is indexing in IR?", "Explain ranking in IR", "Difference between IR and DBMS"],
        ),
        (
            {"artificial intelligence", "ai"},
            "Artificial Intelligence is the field of building systems that can perform tasks requiring human-like intelligence such as reasoning, learning, planning, perception, and language understanding.",
            ["Types of AI", "AI vs ML", "Applications of AI"],
        ),
        (
            {"data mining"},
            "Data mining is the process of discovering useful patterns, trends, and knowledge from large datasets using statistical, machine learning, and database techniques.",
            ["Steps in data mining", "Data mining vs data analytics", "Applications of data mining"],
        ),
        (
            {"data analytics"},
            "Data analytics is the study of data to find patterns, insights, and useful decisions. It usually involves data collection, cleaning, analysis, visualization, and interpretation.",
            ["Types of data analytics", "Data analytics vs data mining", "Applications of data analytics"],
        ),
        (
            {"geography", "physical geography", "human geography"},
            "Geography explores Earth's landscapes, environments, and human activities.\n"
            "- Physical geography covers landforms, climate, ecosystems, and natural hazards.\n"
            "- Human geography studies populations, settlement patterns, agriculture, and urbanization.\n"
            "- Map skills, layers of geography (spatial, environmental, cultural), and fieldwork summaries help for exams.\n\n"
            "Revise by drawing concept maps of regions, comparing physical vs human features, and summarizing key maps in your own words.",
            ["Explain human vs physical geography", "Describe climate zones", "How to revise geography"],
        ),
        (
            {"statistics", "probability"},
            "Probability measures the chance of an event occurring, while statistics deals with collecting, analyzing, and interpreting data. Common topics are mean, median, variance, distributions, hypothesis testing, and correlation.",
            ["What is probability?", "Explain mean median mode", "What is standard deviation?"],
        ),
        (
    
            {"cyber security", "cybersecurity", "security"},
            "Cybersecurity is the practice of protecting systems, networks, and data from attacks. Core areas include authentication, encryption, malware, firewalls, and network security.",
            ["What is encryption?", "Types of cyber attacks", "What is authentication?"],
        ),
        (
            {"blockchain"},
            "Blockchain is a distributed digital ledger where transactions are grouped into blocks and linked securely using cryptography. It is known for decentralization, transparency, and immutability.",
            ["What is blockchain?", "Blockchain vs database", "Applications of blockchain"],
        ),
        (
            {"software engineering", "sdlc"},
            "Software engineering is the disciplined development of software using planning, design, coding, testing, deployment, and maintenance. SDLC models include waterfall, iterative, spiral, and agile.",
            ["What is SDLC?", "Agile vs waterfall", "Software testing types"],
        ),
        (
            {"java"},
            "Java is an object-oriented programming language known for platform independence through the JVM. Important basics are classes, objects, inheritance, interfaces, exceptions, and collections.",
            ["What is JVM?", "OOP in Java", "Java collections"],
        ),
        (
            {"c programming", "language c", " c "},
            "C is a procedural programming language widely used for system programming. Important topics include variables, pointers, arrays, functions, structures, memory management, and file handling.",
            ["What are pointers in C?", "Arrays vs pointers", "Structures in C"],
        ),
        (
            {"economics", "microeconomics", "macroeconomics"},
            "Economics studies how resources are produced, distributed, and consumed. Microeconomics focuses on individuals and firms, while macroeconomics studies the economy as a whole.",
            ["Micro vs macro economics", "What is demand?", "What is inflation?"],
        ),
        (
            {"semiconductor", "semiconductors", "p type", "n type"},
            "A semiconductor is a material with conductivity between a conductor and an insulator. Silicon and germanium are common examples. Important topics include intrinsic semiconductors, extrinsic semiconductors, p-type, n-type, and PN junction devices.",
            ["Intrinsic vs extrinsic semiconductor", "P-type vs n-type semiconductor", "Applications of semiconductors"],
        ),
    ]

    for keys, answer, suggestions in concept_map:
        if any(_contains_phrase(query, key) for key in keys):
            return answer, ["general_education"], suggestions

    return None


async def _log_chat_message(
    db: AsyncSession,
    user_id: str,
    role: str,
    content: str,
    mode: str | None = None,
) -> None:
    if not user_id or not content:
        return
    db.add(
        ChatMessageModel(
            user_id=user_id,
            role=role,
            content=content[:4000],
            mode=mode[:50] if mode else None,
        )
    )
    try:
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()


def _rule_based_answer(
    message: str,
    subjects: list[Subject],
    upcoming_entries: list[ScheduleEntry],
) -> tuple[str, list[str], list[str]]:
    query = _normalize(message)
    tokens = _tokenize(message)

    if not query:
        return (
            "Ask about your subjects, topics, timetable, revisions, or study guidance.",
            [],
            ["What should I study next?", "List my subjects", "Show topics in Data Analytics"],
        )

    if _is_non_educational_query(message):
        return _non_educational_redirect()

    general_answer = _general_educational_answer(message)
    if general_answer is not None:
        return general_answer

    if {"today", "now", "next"} & tokens or "study" in tokens or "schedule" in tokens or "timetable" in tokens:
        if not upcoming_entries:
            return (
                "No upcoming study sessions are scheduled right now.",
                ["schedule"],
                ["Generate a timetable", "List my subjects"],
            )
        next_items = upcoming_entries[:5]
        answer = "Your next study sessions are:\n" + "\n".join(
            f"- {_format_schedule_line(entry)}" for entry in next_items
        )
        return answer, ["schedule"], ["Show topics in a subject", "What should I revise this week?"]

    if "subject" in tokens and ("list" in tokens or "what" in tokens or "have" in tokens):
        if not subjects:
            return (
                "No subjects are saved yet.",
                ["subjects"],
                ["Import syllabus", "Add a subject"],
            )
        return (
            "Your subjects are:\n" + "\n".join(f"- {subject.name}" for subject in subjects),
            ["subjects"],
            [f"Show topics in {subjects[0].name}" if subjects else "Show my timetable"],
        )

    matched_subjects: list[Subject] = []
    for subject in subjects:
        aliases = _subject_aliases(subject.name)
        if any(alias in query for alias in aliases) or (_tokenize(subject.name) & tokens):
            matched_subjects.append(subject)

    if matched_subjects:
        subject = matched_subjects[0]
        ordered_topics = sorted(subject.topics, key=lambda topic: (topic.order_index, topic.name))
        if "topic" in tokens or "syllabus" in tokens or "unit" in tokens or "cover" in tokens:
            topic_lines = [f"- {topic.name}" for topic in ordered_topics[:25]]
            if len(ordered_topics) > 25:
                topic_lines.append(f"- ... and {len(ordered_topics) - 25} more topics")
            return (
                f"{subject.name} has {len(ordered_topics)} topics:\n" + "\n".join(topic_lines),
                [subject.name],
                [f"What should I study next in {subject.name}?", f"How do I revise {subject.name}?"],
            )

        completed = sum(1 for topic in ordered_topics if topic.completed)
        weak_topics = [topic.name for topic in ordered_topics if topic.completion_pct < 40][:5]
        answer_lines = [
            f"{subject.name} has {len(ordered_topics)} topics and {completed} completed topics.",
        ]
        if weak_topics:
            answer_lines.append("Focus next on: " + ", ".join(weak_topics))
        next_subject_sessions = [entry for entry in upcoming_entries if entry.subject_name == subject.name][:3]
        if next_subject_sessions:
            answer_lines.append("Upcoming sessions:")
            answer_lines.extend(f"- {_format_schedule_line(entry)}" for entry in next_subject_sessions)
        return (
            "\n".join(answer_lines),
            [subject.name, "schedule"],
            [f"Show topics in {subject.name}", f"Explain {subject.name} study plan"],
        )

    matched_topics: list[tuple[str, str]] = []
    for subject in subjects:
        for topic in subject.topics:
            topic_tokens = _tokenize(topic.name)
            if len(topic_tokens & tokens) >= 2 or _normalize(topic.name) in query:
                matched_topics.append((subject.name, topic.name))

    if matched_topics:
        grouped: dict[str, list[str]] = defaultdict(list)
        for subject_name, topic_name in matched_topics[:10]:
            grouped[subject_name].append(topic_name)
        lines = ["I found these matching topics:"]
        for subject_name, topic_names in grouped.items():
            lines.append(f"- {subject_name}: {', '.join(topic_names)}")
        return "\n".join(lines), ["topics"], ["Show my next sessions", "List my subjects"]

    educational_guidance = {
        "revision": "For revision, use active recall, short unit-wise notes, and quiz practice after each unit.",
        "quiz": "For quizzes, first review the unit summary, then solve 5-10 questions and check explanations.",
        "exam": "For exam prep, prioritize high-weight units, weak topics, and one revision cycle before the exam.",
        "study": "Use 45-60 minute focused sessions, then a short break. End each session with 3 key takeaways.",
        "process": "Explain the topic in your own words, write key points, solve one example, then test yourself without notes.",
        "algorithm": "For algorithm questions, describe the idea first, then steps, time complexity, space complexity, and one example.",
        "python": "For Python learning, start with syntax, functions, lists/dicts, file handling, and small practice problems.",
        "database": "For database topics, focus on ER modeling, normalization, SQL queries, joins, indexing, and transactions.",
    }
    for key, value in educational_guidance.items():
        if key in tokens:
            return value, ["guidance"], ["What should I study next?", "Show weak topics in a subject"]

    return (
        "I can answer educational questions in general, and I can also use your saved subjects, topics, units, "
        "and timetable when relevant. Ask a concept question, exam-prep question, or study-planning question.",
        [],
        [
            "Explain normalization in DBMS",
            "How do I prepare for an exam in one week?",
            "List my subjects",
        ],
    )


# Runtime fallback: prefer real educational answers even when the LLM is unavailable.
def _general_educational_answer(
    message: str,
    subjects: list[Subject] | None = None,
) -> tuple[str, list[str], list[str]] | None:
    query = _normalize_educational_query(message)
    expanded_query = f" {query} "
    subjects = subjects or []

    if _is_compliment(message):
        return (
            "Thanks! Ask any educational question or tell me a subject or unit to focus on.",
            ["general_education"],
            ["What should I study next?", "Show topics in a subject", "How do I revise this unit?"],
        )

    if any(greeting in expanded_query for greeting in [" hi ", " hello ", " hey "]):
        return (
            "Ask any educational question, concept doubt, exam-prep question, or study-method question. "
            "I can explain topics across programming, computer science, analytics, economics, maths, and more.",
            ["general_education"],
            ["Explain machine learning types", "What is normalization in DBMS?", "How do I prepare for exams?"],
        )

    if _needs_educational_clarification(message):
        return _educational_clarification_redirect()

    dynamic_answer = _build_dynamic_educational_answer(message, subjects)
    if dynamic_answer is not None:
        return dynamic_answer

    if (" tcp " in expanded_query and " udp " in expanded_query) and (
        "compare" in query or "difference between" in query
    ):
        return (
            "TCP and UDP are transport-layer protocols used in computer networks, but they work differently.\n"
            "- TCP is connection-oriented, while UDP is connectionless\n"
            "- TCP is reliable and uses acknowledgements, sequencing, and retransmission\n"
            "- UDP is faster and lighter because it does not guarantee delivery or ordering\n"
            "- TCP is used for web browsing, email, and file transfer\n"
            "- UDP is used for live streaming, online gaming, and DNS\n\n"
            "In short: TCP focuses on reliability, while UDP focuses on speed and low overhead.",
            ["general_education"],
            ["What is TCP?", "What is UDP?", "Explain OSI model"],
        )

    if "finite automata" in expanded_query or " automata " in expanded_query:
        if "types" in query or "what are" in query or "explain" in query:
            return (
                "Finite automata are abstract machines used to recognize patterns and regular languages.\n"
                "Main types are:\n"
                "- DFA: deterministic finite automaton, where each input leads to exactly one next state\n"
                "- NFA: nondeterministic finite automaton, where an input may lead to multiple possible states\n"
                "- e-NFA or NFA with epsilon transitions: allows moves without consuming an input symbol\n"
                "- Moore machine: output depends only on the current state\n"
                "- Mealy machine: output depends on the current state and current input\n\n"
                "In exams, the most common comparison is DFA vs NFA: both recognize regular languages, but DFA has exactly one transition choice while NFA can have multiple possibilities.",
                ["general_education"],
                ["Difference between DFA and NFA", "What is a regular language?", "Explain Mealy vs Moore machine"],
            )

    if "actuator" in expanded_query:
        return (
            "An actuator is a device that converts energy into physical motion or action in a system.\n"
            "Common types are:\n"
            "- Electrical actuators: use electric power, such as motors and solenoids\n"
            "- Hydraulic actuators: use pressurized fluid for high-force movement\n"
            "- Pneumatic actuators: use compressed air for fast and simple motion\n"
            "- Thermal actuators: operate using heat expansion\n"
            "- Mechanical actuators: use gears, screws, or cams to create movement\n\n"
            "Actuators are used in robots, valves, industrial automation, vehicles, and control systems.",
            ["general_education"],
            ["What is a sensor?", "Difference between sensor and actuator", "Applications of IoT"],
        )

    if "sensor" in expanded_query:
        return (
            "A sensor is a device that detects or measures a physical quantity and converts it into a signal that a system can read.\n"
            "Sensors can measure things like temperature, light, pressure, motion, humidity, distance, and sound.\n"
            "Common examples are temperature sensors, infrared sensors, pressure sensors, proximity sensors, and motion sensors.\n"
            "Sensors are used in IoT systems, mobile phones, robots, vehicles, medical devices, and industrial automation.",
            ["general_education"],
            ["Types of sensors", "Difference between sensor and actuator", "Applications of IoT"],
        )

    if "nutrient" in expanded_query or "neutrient" in expanded_query:
        return (
            "Nutrients are substances in food that the body needs for energy, growth, repair, and proper functioning.\n"
            "The main nutrients are carbohydrates, proteins, fats, vitamins, minerals, water, and in many contexts dietary fiber is also discussed.\n"
            "- Carbohydrates give energy\n"
            "- Proteins help growth and tissue repair\n"
            "- Fats store energy and support body functions\n"
            "- Vitamins and minerals help regulate body processes\n"
            "- Water is essential for transport, temperature control, and metabolism\n\n"
            "In short: nutrients are the essential components of food that keep the body healthy and active.",
            ["general_education"],
            ["Types of nutrients", "What are vitamins?", "Balanced diet explanation"],
        )

    concept_map: list[tuple[set[str], str, list[str]]] = [
        (
            {"nlp", "natural language processing"},
            "Natural Language Processing (NLP) is a branch of AI that helps computers understand, interpret, and generate human language.\n"
            "Common tasks in NLP include tokenization, parsing, part-of-speech tagging, named entity recognition, sentiment analysis, translation, question answering, and text summarization.\n"
            "It is used in chatbots, search engines, voice assistants, spell checkers, and translation systems.",
            ["What is tokenization?", "Explain language models", "What is parsing in NLP?"],
        ),
        (
            {"dbms", "normalization", "sql", "database"},
            "DBMS is software used to store, manage, and retrieve structured data efficiently. Important topics include normalization, SQL queries, joins, indexing, transactions, backup, recovery, and concurrency control.",
            ["Explain normalization", "What are joins in SQL?", "What is indexing in DBMS?"],
        ),
        (
            {"ml", "machine learning"},
            "Machine learning is a branch of AI in which systems learn patterns from data and improve predictions or decisions without being explicitly programmed for every case.\n"
            "The main types are supervised learning, unsupervised learning, and reinforcement learning.",
            ["Types of machine learning", "AI vs ML", "What is deep learning?"],
        ),
    ]
    for keys, answer, suggestions in concept_map:
        if any(_contains_phrase(query, key) for key in keys):
            return answer, ["general_education"], suggestions

    if not _is_general_educational_query(message):
        return None

    focus = _extract_focus_phrase(message)
    focus_title = _format_focus_title(focus)
    return (
        f"{focus_title} is an educational topic.\n"
        f"A good answer should explain what {focus} means, the main points related to it, one clear example, and why it is important.\n"
        "Ask again with a little more detail such as the subject or chapter if you want a fuller answer.",
        ["general_education"],
        ["Explain this topic simply", f"Give an exam answer on {focus_title}", f"Give one example of {focus_title}"],
    )


def _rule_based_answer(
    message: str,
    subjects: list[Subject],
    upcoming_entries: list[ScheduleEntry],
) -> tuple[str, list[str], list[str]]:
    query = _normalize(message)
    tokens = _tokenize(message)

    if not query:
        return (
            "Ask about your saved subjects, topics, timetable, or revision status.",
            ["study_data"],
            ["List my subjects", "Show my schedule", "What should I study next?"],
        )

    if _is_non_educational_query(message):
        return _non_educational_redirect()

    general_answer = _general_educational_answer(message, subjects)
    if general_answer is not None:
        return general_answer

    if {"today", "now", "next"} & tokens or "schedule" in tokens or "timetable" in tokens:
        if not upcoming_entries:
            return (
                "No upcoming study sessions are scheduled right now.",
                ["schedule"],
                ["List my subjects", "Show topics in a subject"],
            )
        next_items = upcoming_entries[:5]
        answer = "Your next study sessions are:\n" + "\n".join(
            f"- {_format_schedule_line(entry)}" for entry in next_items
        )
        return answer, ["schedule"], ["List my subjects", "Show topics in a subject"]

    if "subject" in tokens and ("list" in tokens or "what" in tokens or "have" in tokens or "show" in tokens):
        if not subjects:
            return (
                "No subjects are saved yet.",
                ["subjects"],
                ["Import syllabus", "Add a subject"],
            )
        return (
            "Your subjects are:\n" + "\n".join(f"- {subject.name}" for subject in subjects),
            ["subjects"],
            [f"Show topics in {subjects[0].name}" if subjects else "Show my schedule"],
        )

    matched_subjects: list[Subject] = []
    for subject in subjects:
        aliases = _subject_aliases(subject.name)
        if any(alias in query for alias in aliases) or (_tokenize(subject.name) & tokens):
            matched_subjects.append(subject)

    if matched_subjects:
        subject = matched_subjects[0]
        ordered_topics = sorted(subject.topics, key=lambda topic: (topic.order_index, topic.name))
        if "topic" in tokens or "syllabus" in tokens or "unit" in tokens or "cover" in tokens:
            topic_lines = [f"- {topic.name}" for topic in ordered_topics[:25]]
            if len(ordered_topics) > 25:
                topic_lines.append(f"- ... and {len(ordered_topics) - 25} more topics")
            return (
                f"{subject.name} has {len(ordered_topics)} topics:\n" + "\n".join(topic_lines),
                [subject.name],
                [f"What should I study next in {subject.name}?", "Show my schedule"],
            )

        next_subject_sessions = [entry for entry in upcoming_entries if entry.subject_name == subject.name][:3]
        lines = [f"{subject.name} has {len(ordered_topics)} saved topics."]
        if next_subject_sessions:
            lines.append("Upcoming sessions:")
            lines.extend(f"- {_format_schedule_line(entry)}" for entry in next_subject_sessions)
        return "\n".join(lines), [subject.name, "schedule"], [f"Show topics in {subject.name}", "What should I study next?"]

    matched_topics: list[tuple[str, str]] = []
    for subject in subjects:
        for topic in subject.topics:
            topic_tokens = _tokenize(topic.name)
            if len(topic_tokens & tokens) >= 2 or _normalize(topic.name) in query:
                matched_topics.append((subject.name, topic.name))

    if matched_topics:
        grouped: dict[str, list[str]] = defaultdict(list)
        for subject_name, topic_name in matched_topics[:10]:
            grouped[subject_name].append(topic_name)
        lines = ["I found these matching topics:"]
        for subject_name, topic_names in grouped.items():
            lines.append(f"- {subject_name}: {', '.join(topic_names)}")
        return "\n".join(lines), ["topics"], ["Show my schedule", "List my subjects"]

    return (
        "I can use your saved study data for subjects, topics, and timetable. Ask about your schedule, subjects, or saved topics.",
        ["study_data"],
        ["List my subjects", "Show my schedule", "Show topics in a subject"],
    )


async def _llm_answer(
    message: str,
    subjects: list[Subject],
    upcoming_entries: list[ScheduleEntry],
    history: list[ChatMessage],
) -> tuple[str | None, str | None]:
    get_settings.cache_clear()
    settings = get_settings()
    if not settings.groq_api_key:
        return None, None

    is_study_data_query = _is_study_data_query(message)
    subject_summaries = []
    if is_study_data_query:
        for subject in subjects[:5]:
            ordered_topics = sorted(subject.topics, key=lambda topic: (topic.order_index, topic.name))
            topic_preview = ", ".join(topic.name for topic in ordered_topics[:6])
            suffix = f", ... (+{len(ordered_topics) - 6} more)" if len(ordered_topics) > 6 else ""
            subject_summaries.append(f"{subject.name}: {topic_preview}{suffix}")

    schedule_preview = "\n".join(_format_schedule_line(entry) for entry in upcoming_entries[:5]) if is_study_data_query else ""
    system_prompt = (
        "You are an education-only assistant for a study planner app. "
        "Answer only educational questions. "
        "Be clear, accurate, and natural. "
        "For concept questions, give a definition, key points, and one example. "
        "For comparisons, give direct differences. "
        "For algorithms, give steps and time complexity. "
        "For code, provide working code when asked. "
        "If the question is outside education, briefly refuse and redirect to an educational topic. "
        "If the term is ambiguous or misspelled, ask for clarification instead of guessing. "
        "History, civics, political science, economics, constitution, government roles, and general-knowledge questions about public offices are educational and should be answered normally. "
        "For common computer-science abbreviations, prefer the standard academic meaning unless the user gives another domain. "
        "Examples: NLP = Natural Language Processing, RAG = Retrieval-Augmented Generation, DBMS = Database Management System, OS = Operating System."
    )
    base_messages = [{"role": "system", "content": system_prompt}]
    if is_study_data_query:
        context_prompt = (
            "Student context:\n"
            f"Subjects and topics:\n" + ("\n".join(subject_summaries) or "No subjects saved.") + "\n\n"
            f"Upcoming schedule:\n{schedule_preview or 'No upcoming schedule.'}"
        )
        base_messages.append({"role": "system", "content": context_prompt})
    single_topic_base_messages = list(base_messages)

    candidate_models: list[str] = []
    for model in [settings.groq_model, "llama-3.1-8b-instant"]:
        if model and model not in candidate_models:
            candidate_models.append(model)

    intent = _question_intent(message)
    multi_topic_explain = intent in {"explain", "types", "compare", "how"} and (
        message.count(",") >= 1 or len(re.findall(r"\b(and|vs|versus)\b", message.lower())) >= 1
    )
    extracted_topics = _extract_multi_topics(message) if multi_topic_explain else []
    wants_detailed_answer = _wants_detailed_answer(message)
    if multi_topic_explain:
        base_messages.append(
            {
                "role": "system",
                "content": (
                    "The user is asking about multiple topics in one question. "
                    "Cover every topic in one answer. "
                    "If the user did not explicitly ask for detail, keep each topic brief and balanced. "
                    "Do not over-expand the first topic and do not stop before covering all topics."
                ),
            }
        )
    if intent in {"explain", "types", "compare", "how", "exam_plan"}:
        max_tokens = 1200 if multi_topic_explain else 800
    elif intent == "code":
        max_tokens = 700
    else:
        max_tokens = 320
    history_sets = [
        history[-4:],
        history[-2:],
        [],
    ]

    async def _groq_complete(
        client: httpx.AsyncClient,
        model: str,
        messages: list[dict[str, str]],
        token_limit: int,
    ) -> tuple[str | None, str | None]:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.groq_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": token_limit,
            },
        )
        response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]
        content = (choice.get("message") or {}).get("content", "").strip()
        finish_reason = choice.get("finish_reason")
        return content or None, finish_reason

    if len(extracted_topics) >= 2:
        last_error = None
        for model in candidate_models:
            try:
                async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
                    sections: list[str] = []
                    for topic in extracted_topics:
                        if wants_detailed_answer:
                            topic_prompt = (
                                f"Explain only {topic} as an algorithm. "
                                "Do not mention any other algorithm. "
                                "Use a concise exam-ready format with Definition, 2 or 3 Key points, one short Use case, and if useful one line on Steps or Time complexity. "
                                "Keep it under 170 words."
                            )
                            topic_token_limit = 240
                            continuation_limit = 80
                        else:
                            topic_prompt = (
                                f"Explain only {topic} as an algorithm. "
                                "Do not mention any other algorithm. "
                                "Do not add an introduction. "
                                "Use this exact brief format: "
                                "Definition: ... Key points: - ... - ... Use case: ... "
                                "Keep it under 90 words."
                            )
                            topic_token_limit = 130
                            continuation_limit = 40
                        topic_messages = list(single_topic_base_messages)
                        topic_messages.append(
                            {
                                "role": "user",
                                "content": topic_prompt,
                            }
                        )
                        content, finish_reason = await _groq_complete(client, model, topic_messages, topic_token_limit)
                        if content and finish_reason == "length":
                            followup_messages = list(topic_messages)
                            followup_messages.append({"role": "assistant", "content": content})
                            followup_messages.append(
                                {
                                    "role": "user",
                                    "content": "Finish the remaining line briefly without repeating anything.",
                                }
                            )
                            extra, _ = await _groq_complete(client, model, followup_messages, continuation_limit)
                            if extra:
                                content = _merge_continuation(content, extra)
                        if not content:
                            raise RuntimeError(f"Empty Groq answer for topic: {topic}")
                        sections.append(f"**{topic}**\n{content.strip()}")
                    if sections:
                        return "\n\n".join(sections), "groq" if model == settings.groq_model else "groq_backup"
            except Exception as exc:
                last_error = exc
                continue

    last_error = None
    for model in candidate_models:
        for short_history in history_sets:
            messages = list(base_messages)
            for item in short_history:
                messages.append({"role": item.role, "content": item.content[:1200]})
            messages.append({"role": "user", "content": message})

            for _ in range(2):
                try:
                    async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
                        content, finish_reason = await _groq_complete(client, model, messages, max_tokens)
                        continuation_count = 0
                        while content and (
                            finish_reason == "length" or _looks_truncated_answer(content)
                        ) and continuation_count < 2:
                            continuation_messages = list(messages)
                            continuation_messages.append({"role": "assistant", "content": content})
                            continuation_messages.append(
                                {
                                    "role": "user",
                                    "content": "Continue from exactly where you stopped. Do not repeat earlier lines or headings. Complete the remaining explanation briefly and end with a proper closing sentence.",
                                }
                            )
                            extra, finish_reason = await _groq_complete(client, model, continuation_messages, max_tokens // 2)
                            if not extra:
                                break
                            content = _merge_continuation(content, extra)
                            continuation_count += 1
                    if content:
                        return content, "groq" if model == settings.groq_model else "groq_backup"
                except Exception as exc:
                    last_error = exc
                    continue

    if last_error is not None:
        logger.warning("Groq chat generation failed for message=%r error=%s", message[:120], repr(last_error))

    return None, "groq_fallback"


@router.get("/history/{user_id}", response_model=list[ChatHistoryItem])
async def chat_history(user_id: str, db: AsyncSession = Depends(get_db)):
    """Return the recent chat log for a user."""
    result = await db.execute(
        select(ChatMessageModel)
        .where(ChatMessageModel.user_id == user_id)
        .order_by(ChatMessageModel.created_at.desc())
        .limit(100)
    )
    messages = result.scalars().all()
    return [ChatHistoryItem.from_orm(msg) for msg in reversed(messages)]


@router.post("/ask", response_model=ChatAskResponse)
async def ask_chatbot(payload: ChatAskRequest, db: AsyncSession = Depends(get_db)):
    get_settings.cache_clear()
    settings = get_settings()
    await _log_chat_message(db, payload.user_id, "user", payload.message, "user_query")
    if _is_non_educational_query(payload.message):
        answer, sources, suggestions = _non_educational_redirect()
        await _log_chat_message(db, payload.user_id, "assistant", answer, "education_only")
        return ChatAskResponse(
            answer=answer,
            sources=sources,
            suggestions=suggestions,
            mode="education_only",
        )

    result = await db.execute(
        select(Subject)
        .where(Subject.user_id == payload.user_id)
        .options(selectinload(Subject.topics))
    )
    subjects = list(result.scalars().unique().all())

    schedule_result = await db.execute(
        select(ScheduleEntry)
        .where(ScheduleEntry.user_id == payload.user_id, ScheduleEntry.completed == 0)
        .order_by(ScheduleEntry.scheduled_date, ScheduleEntry.start_time)
    )
    upcoming_entries = list(schedule_result.scalars().all())

    ai_answer, provider = await _llm_answer(
        payload.message,
        subjects,
        upcoming_entries,
        payload.history,
    )
    if ai_answer:
        await _log_chat_message(db, payload.user_id, "assistant", ai_answer, provider or "llm")
        return ChatAskResponse(
            answer=ai_answer,
            sources=["subjects", "topics", "schedule"],
            suggestions=["What should I study next?", "Show topics in a subject", "How do I revise this unit?"],
            mode=provider or "llm",
        )

    groq_required_for_concepts = (
        settings.require_groq
        and bool(settings.groq_api_key)
        and _is_general_educational_query(payload.message)
        and not _is_study_data_query(payload.message)
    )
    if groq_required_for_concepts:
        answer, sources, suggestions = _llm_temporarily_unavailable_answer()
        mode = provider or "groq_unavailable"
        await _log_chat_message(db, payload.user_id, "assistant", answer, mode)
        return ChatAskResponse(
            answer=answer,
            sources=sources,
            suggestions=suggestions,
            mode=mode,
        )

    answer, sources, suggestions = _rule_based_answer(
        payload.message,
        subjects,
        upcoming_entries,
    )
    mode = "rule_based_fallback" if provider else "rule_based"

    await _log_chat_message(db, payload.user_id, "assistant", answer, mode)
    return ChatAskResponse(
        answer=answer,
        sources=sources,
        suggestions=suggestions,
        mode=mode,
    )
