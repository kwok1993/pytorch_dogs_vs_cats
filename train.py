"""
__author__      = 'kwok'
__time__        = '2021/6/22 17:35'
"""
import torch
import torch.nn as nn
from torchvision import transforms, models
from torch.utils.data import DataLoader
from utils.torch_utils import select_device
from utils.folder import ImageFolder
import os
from tqdm import tqdm
import argparse
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(pathname)s->%(funcName)s[line:%(lineno)d] %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)


def train():
    labels = opt.labels
    model_weights_path = opt.weights
    epochs = opt.epochs
    save_path = opt.save_path
    path = opt.dataset_path
    batch_size = opt.batch_size
    # 查找可用 GPU
    device = select_device(device=opt.device, batch_size=batch_size)

    train_transform = transforms.Compose([
        # 图片随机裁剪为不同的大小和宽高比, 再统一尺寸
        transforms.RandomResizedCrop(224),
        # 图片水平翻转, 默认概率为 0.5
        transforms.RandomHorizontalFlip(),
        # 将图片数据转为Tensor
        transforms.ToTensor(),
        # 数据标准化
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])

    val_transform = transforms.Compose([
        # 图片统一尺寸
        transforms.Resize(256),
        # 中间区域裁剪
        transforms.CenterCrop(224),
        # 将图片数据转为Tensor
        transforms.ToTensor(),
        # 数据标准化
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])
    # 加载训练集图片数据
    # torchvision 实现的 ImageFolder 是读取训练集子目录名 sort() 排序后作为 class name, 没法自定义class index, 所以自己重写一个
    train_dataset = ImageFolder(root=os.path.join(path, 'train'), transform=train_transform, labels=labels)
    # 打乱图片数据，并根据 batch_size 分批
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_dataset = ImageFolder(root=os.path.join(path, 'val'), transform=val_transform, labels=labels)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=True)

    '''
    model_urls = {
        'resnet18': 'https://download.pytorch.org/models/resnet18-5c106cde.pth',
        'resnet34': 'https://download.pytorch.org/models/resnet34-333f7ec4.pth',
        'resnet50': 'https://download.pytorch.org/models/resnet50-19c8e357.pth',
        'resnet101': 'https://download.pytorch.org/models/resnet101-5d3b4d8f.pth',
        'resnet152': 'https://download.pytorch.org/models/resnet152-b121ed2d.pth',
        'resnext50_32x4d': 'https://download.pytorch.org/models/resnext50_32x4d-7cdf4587.pth',
        'resnext101_32x8d': 'https://download.pytorch.org/models/resnext101_32x8d-8ba56ff5.pth',
        'wide_resnet50_2': 'https://download.pytorch.org/models/wide_resnet50_2-95faca4d.pth',
        'wide_resnet101_2': 'https://download.pytorch.org/models/wide_resnet101_2-32ee1156.pth',
    }
    '''
    # 使用 torchvision 做迁移训练,使用 models.resnet50(),就下载 resnet50 对应的预训练模型,以此类推
    # 如果想程序自动下载预训练模型，则使用 model = models.resnet50(pretrained=True), 然后根据下载路径获取预训练模型
    model = models.resnet50()
    # 多 GPU 训练时，需要将模型并行化，需要使用 DataParallel 来操作
    if torch.cuda.device_count() > 1:
        model = nn.DataParallel(model)
    # torch.load() 加载预训练模型 => map_location 映射存储位置, 因为使用GPU训练, 当前 device 是 cuda:0 即第一块GPU
    # model.load_state_dict() 加载预训练模型参数
    model.load_state_dict(torch.load(model_weights_path, map_location=device))
    # 固定预训练模型特征层参数，不做梯度更新
    for param in model.parameters():
        param.requires_grad = False
    # 获取预训练模型最后全连接层的输入特征（in_features）
    in_channel = model.fc.in_features
    # 预训练模型默认的 out_features 为 ImageNet 的 1000 分类
    # 替换预训练模型最后全连接层的输出特征（out_features）, 即自己数据集 number_classes,
    model.fc = torch.nn.Linear(in_channel, out_features=2)
    # 修改后的模型加载到指定 GPU
    model.to(device)
    # 损失函数： resnet 分类问题选用交叉熵损失函数
    loss_function = nn.CrossEntropyLoss()
    # 优化器
    # 尽管 Adam（自适应优化算法） 在训练集上的 loss 更小，训练阶段前期收敛更快，但是在测试集上的 loss 比 SGD（随机梯度下降算法） 高
    # optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
    # 最佳准确率初始化
    best_acc = 0.0
    # batch 训练次数, train_loader 是根据 batch_size 做好分批的
    train_steps = len(train_loader)
    logger.info('Start Training')
    # 训练 N 个 epoch
    for epoch in range(epochs):
        # 模型训练阶段, 模型启用 batch normalization 和 drop out
        model.train()
        # 训练阶段损失值
        train_loss = 0.0
        # 实现训练阶段进度条效果
        train_bar = tqdm(train_loader, desc='train epoch[{}/{}] loss:{:.3f}'.format(epoch + 1, epochs, train_loss), ncols=100)
        for train_data in train_bar:
            # 获取训练集图片数据和标签数据
            images, labels = train_data
            # 优化器梯度归零
            optimizer.zero_grad()
            # 损失值计算
            loss = loss_function(model(images.to(device)), labels.to(device))
            # 反向传播计算每个参数梯度值
            loss.backward()
            # 通过梯度下降更新参数
            # optimizer.step() 应该放在每一个 bitch 训练中, 而不是 epoch 中, 每次 batch 训练看作一次训练, 一次训练更新一次参数
            optimizer.step()
            # 获取张量中的元素值, 更新训练阶段损失值
            train_loss += loss.item()
            # 更新本次 batch 训练结果
            train_bar.desc = 'train epoch[{}/{}] loss:{:.3f}'.format(epoch + 1, epochs, loss)

        # 模型验证阶段, 模型不启用 batch normalization 和 drop out
        model.eval()
        # 准确值初始化
        acc = 0.0
        # 屏蔽参数跟踪, 不计算损失梯度等参数
        with torch.no_grad():
            # 实现验证阶段进度条效果
            val_bar = tqdm(val_loader, desc='valid epoch[{}/{}]'.format(epoch + 1, epochs), ncols=100)
            for val_data in val_bar:
                # 获取验证集图片数据和标签数据
                val_images, val_labels = val_data
                # 模型推理
                outputs = model(val_images.to(device))
                # 获取每张图片推理置信度最高的结果, 索引维度 dim=0 按列, dim=1 按行, 一般是按行获取
                predict_y = torch.max(outputs, dim=1)[1]
                # eq() 逐个元素比较两个张量是否相等, 返回的张量包含各个位置上的布尔值
                # sum() 求和
                # item() 获取张量元素值
                acc += torch.eq(predict_y, val_labels.to(device)).sum().item()
                # 更新本次 batch 验证结果
                val_bar.desc = 'valid epoch[{}/{}]'.format(epoch + 1, epochs)

        val_num = len(val_dataset)
        val_accurate = acc / val_num
        logger.info('[epoch {}] train_loss: {:.3f}, val_accuracy: {:.3f}'.format(epoch + 1, train_loss / train_steps, val_accurate))

        if val_accurate > best_acc:
            best_acc = val_accurate
            # 保存模型, 仅保存参数
            torch.save(model.state_dict(), save_path)
    logger.info('Finished Training')
    logger.info('The model has been saved in : {}'.format(save_path))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--weights', type=str, default='weights/resnet50-19c8e357.pth', help='预训练模型路径')
    parser.add_argument('--device', default='', help='可使用的GPU, 支持输入 0 或 0,1,2,3 或 cpu')
    parser.add_argument('--epochs', type=int, default=10)
    parser.add_argument('--save-path', type=str, default='weights/my_resnet50.pt', help='训练完成后模型保存路径')
    parser.add_argument('--dataset-path', type=str, default='../dataset', help='数据集路径')
    parser.add_argument('--batch-size', type=int, default=16, help='批量大小')
    parser.add_argument('--labels', type=list, default=['dog', 'cat'], help='数据集标签')
    opt = parser.parse_args()
    train()
