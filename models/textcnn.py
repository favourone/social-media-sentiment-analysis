# -*- coding: utf-8 -*-
"""
TextCNN 情感分类模型（PyTorch 版本）
====================================
数学原理：
---------
1. 词嵌入层：将词索引映射为稠密向量
   x_i ∈ R^d  (d=embedding_dim)

2. 卷积层：使用不同大小的卷积核提取 n-gram 特征
   卷积核 w ∈ R^(k×d)，其中 k 为窗口大小（3,4,5）
   c_i = relu(w · x_{i:i+k-1} + b)

3. 池化层：对每个卷积核的输出取最大值（MaxPooling）
   ĉ = max(c_1, c_2, ..., c_n)

4. 全连接层 + Softmax：
   y = softmax(W · [ĉ_1; ĉ_2; ...; ĉ_m] + b)

损失函数：交叉熵
   L = -Σ y_true × log(y_pred)
"""

import os
import sys
import json
import numpy as np

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torch.utils.data import DataLoader, TensorDataset
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


class TextCNN(nn.Module):
    """
    TextCNN 情感分类模型（PyTorch）

    结构：
    Input → Embedding → [Conv1D(k=3), Conv1D(k=4), Conv1D(k=5)] → Concat → Dense → Output

    ┌─────────────────────────────────────┐
    │ Input: (batch, max_seq_length)      │  输入：词索引序列
    ├─────────────────────────────────────┤
    │ Embedding: (batch, seq_len, d)      │  词嵌入
    ├──────────┬──────────┬───────────────┤
    │ Conv1D   │ Conv1D   │ Conv1D        │  多尺度卷积
    │ k=3      │ k=4      │ k=5           │  提取不同长度的 n-gram 特征
    ├──────────┼──────────┼───────────────┤
    │ ReLU     │ ReLU     │ ReLU          │  激活函数
    ├──────────┼──────────┼───────────────┤
    │ MaxPool  │ MaxPool  │ MaxPool       │  取每个卷积核的最大激活值
    ├──────────┴──────────┴───────────────┤
    │ Concatenate                         │  拼接所有特征
    ├─────────────────────────────────────┤
    │ Dropout(0.5)                        │  防止过拟合
    ├─────────────────────────────────────┤
    │ Linear(64) + ReLU                   │  全连接层
    ├─────────────────────────────────────┤
    │ Linear(2) + Softmax                 │  输出：正面/负面概率
    └─────────────────────────────────────┘
    """

    def __init__(self, vocab_size, embedding_dim=None, max_seq_length=None,
                 num_filters=None, filter_sizes=None, num_classes=None, dropout=None):
        super(TextCNN, self).__init__()

        self.embedding_dim = embedding_dim or config.EMBEDDING_DIM
        self.max_seq_length = max_seq_length or config.MAX_SEQ_LENGTH
        self.num_filters = num_filters or config.TEXTCNN_NUM_FILTERS
        self.filter_sizes = filter_sizes or config.TEXTCNN_FILTER_SIZES
        self.num_classes = num_classes or config.TEXTCNN_NUM_CLASSES
        self.dropout_rate = dropout or config.TEXTCNN_DROPOUT

        # 词嵌入层
        # 数学：将离散的词索引映射到连续的向量空间
        # 输入: (batch, seq_len) -> 输出: (batch, seq_len, embedding_dim)
        self.embedding = nn.Embedding(
            num_embeddings=vocab_size,
            embedding_dim=self.embedding_dim,
            padding_idx=0  # PAD token
        )

        # 多尺度卷积层
        # 使用不同大小的卷积核（3,4,5）捕获不同长度的 n-gram 特征
        # 每个卷积核独立提取特征，然后拼接
        self.convs = nn.ModuleList([
            nn.Conv1d(
                in_channels=self.embedding_dim,
                out_channels=self.num_filters,
                kernel_size=fs
            )
            for fs in self.filter_sizes
        ])

        # Dropout 防止过拟合
        self.dropout = nn.Dropout(self.dropout_rate)

        # 全连接层
        # 输入维度 = 卷积核数量 × 每种卷积核的输出维度
        fc_input_dim = self.num_filters * len(self.filter_sizes)
        self.fc1 = nn.Linear(fc_input_dim, 64)
        self.fc2 = nn.Linear(64, self.num_classes)

    def forward(self, x):
        """
        前向传播

        参数：
            x: 输入张量，shape=(batch_size, max_seq_length)
        返回：
            logits: 输出张量，shape=(batch_size, num_classes)
        """
        # 1. 词嵌入
        # (batch, seq_len) -> (batch, seq_len, embed_dim)
        embedded = self.embedding(x)

        # 2. 调整维度用于 Conv1d
        # Conv1d 期望输入: (batch, channels, length)
        # (batch, seq_len, embed_dim) -> (batch, embed_dim, seq_len)
        embedded = embedded.permute(0, 2, 1)

        # 3. 多尺度卷积 + ReLU + 最大池化
        # 每个卷积核提取不同长度的 n-gram 特征
        conv_outputs = []
        for conv in self.convs:
            # 卷积: (batch, num_filters, seq_len - kernel_size + 1)
            c = conv(embedded)
            # ReLU 激活
            c = F.relu(c)
            # 最大池化: (batch, num_filters, 1) -> (batch, num_filters)
            # 数学：ĉ = max(c_1, c_2, ..., c_n-k+1)
            c = F.max_pool1d(c, c.size(2)).squeeze(2)
            conv_outputs.append(c)

        # 4. 拼接所有卷积核的输出
        # (batch, num_filters * len(filter_sizes))
        merged = torch.cat(conv_outputs, dim=1)

        # 5. Dropout
        merged = self.dropout(merged)

        # 6. 全连接层
        # (batch, 64)
        out = F.relu(self.fc1(merged))
        # (batch, num_classes)
        logits = self.fc2(out)

        return logits


class TextCNNModel:
    """
    TextCNN 模型封装类
    提供训练、预测、保存、加载等功能
    """

    def __init__(self, vocab_size=None, embedding_dim=None, max_seq_length=None):
        self.vocab_size = vocab_size or config.MAX_VOCAB_SIZE
        self.embedding_dim = embedding_dim or config.EMBEDDING_DIM
        self.max_seq_length = max_seq_length or config.MAX_SEQ_LENGTH
        self.model = None
        self.device = None
        self.history = []  # 训练历史

    def build_model(self):
        """构建 TextCNN 模型"""
        if not HAS_TORCH:
            print("⚠️  PyTorch 未安装")
            return None

        torch.manual_seed(config.RANDOM_SEED)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(config.RANDOM_SEED)

        # 自动选择设备
        if torch.cuda.is_available():
            self.device = torch.device('cuda')
            print(f"   使用 GPU: {torch.cuda.get_device_name(0)}")
        else:
            self.device = torch.device('cpu')
            print(f"   使用 CPU")

        # 构建模型
        self.model = TextCNN(
            vocab_size=self.vocab_size,
            embedding_dim=self.embedding_dim,
            max_seq_length=self.max_seq_length
        ).to(self.device)

        # 打印模型结构
        total_params = sum(p.numel() for p in self.model.parameters())
        trainable_params = sum(p.numel() for p in self.model.parameters() if p.requires_grad)
        print(f"   模型参数量: {total_params:,} (可训练: {trainable_params:,})")
        print(f"   模型结构:\n{self.model}")

        return self.model

    def train(self, X_train, y_train, X_val=None, y_val=None):
        """
        训练模型

        参数：
            X_train: 训练数据，shape=(n_samples, max_seq_length)
            y_train: 训练标签，0=负面, 1=正面
            X_val: 验证数据
            y_val: 验证标签
        """
        if not HAS_TORCH or self.model is None:
            print("⚠️  模型未初始化")
            return None

        # 转换为 PyTorch 张量
        X_train_t = torch.LongTensor(X_train)
        y_train_t = torch.LongTensor(y_train)
        train_dataset = TensorDataset(X_train_t, y_train_t)
        train_loader = DataLoader(
            train_dataset,
            batch_size=config.TEXTCNN_BATCH_SIZE,
            shuffle=True,
            generator=torch.Generator().manual_seed(config.RANDOM_SEED),
        )

        # 验证集
        val_loader = None
        if X_val is not None and y_val is not None:
            X_val_t = torch.LongTensor(X_val)
            y_val_t = torch.LongTensor(y_val)
            val_dataset = TensorDataset(X_val_t, y_val_t)
            val_loader = DataLoader(val_dataset, batch_size=config.TEXTCNN_BATCH_SIZE)

        # 优化器和损失函数
        optimizer = torch.optim.Adam(
            self.model.parameters(),
            lr=config.TEXTCNN_LEARNING_RATE
        )
        criterion = nn.CrossEntropyLoss()

        # 训练循环
        best_val_acc = float('-inf')
        best_checkpoint_saved = False
        patience = 3
        patience_counter = 0

        print(f"\n   开始训练 (epochs={config.TEXTCNN_EPOCHS})...")
        for epoch in range(config.TEXTCNN_EPOCHS):
            # ---- 训练阶段 ----
            self.model.train()
            total_loss = 0
            correct = 0
            total = 0

            for batch_X, batch_y in train_loader:
                batch_X = batch_X.to(self.device)
                batch_y = batch_y.to(self.device)

                # 前向传播
                outputs = self.model(batch_X)
                loss = criterion(outputs, batch_y)

                # 反向传播
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

                # 统计
                total_loss += loss.item()
                _, predicted = torch.max(outputs, 1)
                total += batch_y.size(0)
                correct += (predicted == batch_y).sum().item()

            train_acc = correct / total
            avg_loss = total_loss / len(train_loader)

            # ---- 验证阶段 ----
            val_acc = 0
            val_loss = None
            if val_loader:
                val_metrics = self._evaluate(val_loader, criterion)
                val_acc = val_metrics['accuracy']
                val_loss = val_metrics['loss']

            # 记录历史
            self.history.append({
                'epoch': epoch + 1,
                'loss': avg_loss,
                'train_loss': avg_loss,
                'val_loss': val_loss,
                'train_acc': train_acc,
                'val_acc': val_acc,
            })

            # 打印进度
            if (epoch + 1) % 2 == 0 or epoch == 0:
                print(f"   Epoch {epoch+1:3d}/{config.TEXTCNN_EPOCHS} | "
                      f"Loss: {avg_loss:.4f} | "
                      f"Train Acc: {train_acc:.4f} | "
                      f"Val Acc: {val_acc:.4f}")

            # Early Stopping
            if val_loader and val_acc > best_val_acc:
                best_val_acc = val_acc
                patience_counter = 0
                # 保存最佳模型
                self._save_checkpoint('textcnn_best.pt')
                best_checkpoint_saved = True
            elif val_loader:
                patience_counter += 1
                if patience_counter >= patience:
                    print(f"   Early stopping at epoch {epoch+1}")
                    break

        best_text = f'{best_val_acc:.4f}' if val_loader else '未提供验证集'
        print(f"\n   ✅ 训练完成！最佳验证准确率: {best_text}")

        # 加载最佳模型
        if best_checkpoint_saved:
            self._load_checkpoint('textcnn_best.pt')

        return self.history

    def _evaluate(self, data_loader, criterion=None):
        """计算验证准确率和真实验证损失。"""
        self.model.eval()
        correct = 0
        total = 0
        total_loss = 0.0
        criterion = criterion or nn.CrossEntropyLoss()

        with torch.no_grad():
            for batch_X, batch_y in data_loader:
                batch_X = batch_X.to(self.device)
                batch_y = batch_y.to(self.device)
                outputs = self.model(batch_X)
                total_loss += criterion(outputs, batch_y).item()
                _, predicted = torch.max(outputs, 1)
                total += batch_y.size(0)
                correct += (predicted == batch_y).sum().item()

        return {
            'accuracy': correct / total if total else 0.0,
            'loss': total_loss / len(data_loader) if len(data_loader) else 0.0,
        }

    def predict(self, texts_sequences):
        """
        预测情感

        参数：
            texts_sequences: 文本索引序列
        返回：
            predictions: 预测结果列表
        """
        if not HAS_TORCH or self.model is None:
            print("⚠️  模型未训练")
            return []

        self.model.eval()
        X = torch.LongTensor(texts_sequences).to(self.device)

        with torch.no_grad():
            outputs = self.model(X)
            probs = F.softmax(outputs, dim=1).cpu().numpy()

        predictions = []
        for prob in probs:
            label = int(np.argmax(prob))
            predictions.append({
                'label': label,
                'label_text': config.SENTIMENT_LABELS[label],
                'probability': float(prob[label]),
                'positive_prob': float(prob[1]),
                'negative_prob': float(prob[0]),
            })
        return predictions

    def evaluate(self, X_test, y_test):
        """评估模型"""
        if not HAS_TORCH or self.model is None:
            return {}

        X_test_t = torch.LongTensor(X_test)
        y_test_t = torch.LongTensor(y_test)
        test_dataset = TensorDataset(X_test_t, y_test_t)
        test_loader = DataLoader(test_dataset, batch_size=config.TEXTCNN_BATCH_SIZE)

        criterion = nn.CrossEntropyLoss()
        self.model.eval()
        total_loss = 0
        correct = 0
        total = 0

        with torch.no_grad():
            for batch_X, batch_y in test_loader:
                batch_X = batch_X.to(self.device)
                batch_y = batch_y.to(self.device)
                outputs = self.model(batch_X)
                loss = criterion(outputs, batch_y)
                total_loss += loss.item()
                _, predicted = torch.max(outputs, 1)
                total += batch_y.size(0)
                correct += (predicted == batch_y).sum().item()

        return {
            'loss': total_loss / len(test_loader),
            'accuracy': correct / total
        }

    def _save_checkpoint(self, filename):
        """保存模型检查点"""
        os.makedirs(config.MODEL_DIR, exist_ok=True)
        path = os.path.join(config.MODEL_DIR, filename)
        torch.save({
            'model_state_dict': self.model.state_dict(),
            'vocab_size': self.vocab_size,
            'embedding_dim': self.embedding_dim,
            'max_seq_length': self.max_seq_length,
        }, path)

    def _load_checkpoint(self, filename):
        """加载模型检查点"""
        path = os.path.join(config.MODEL_DIR, filename)
        if os.path.exists(path):
            checkpoint = torch.load(path, map_location=self.device)
            self.model.load_state_dict(checkpoint['model_state_dict'])

    def save(self, filename='textcnn_model.pt'):
        """保存模型"""
        self._save_checkpoint(filename)
        path = os.path.join(config.MODEL_DIR, filename)
        print(f"   ✅ 模型已保存：{path}")

    def load(self, filename='textcnn_model.pt'):
        """加载模型"""
        if not HAS_TORCH:
            return
        path = os.path.join(config.MODEL_DIR, filename)
        if os.path.exists(path):
            self._load_checkpoint(filename)
            print(f"   ✅ 模型已加载：{path}")
        else:
            print(f"   ⚠️  模型文件不存在：{path}")
